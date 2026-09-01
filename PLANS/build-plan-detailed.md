# agentdag Implementation Plan - DETAILED (M0, S0, M1, D2, M2, M3)

> **The `RESEARCH/` paths point into a private companion repo.** These documents were written
> beside a private research repository and cite it by repo-qualified path for the design
> documents, probe scripts and measurement notes they were derived from. The `RESEARCH/` prefix
> names that repo; it is deliberately not a relative path, because no relative path from here
> resolves to it. These citations do not resolve in a clone of this repo. They are kept rather than stripped because a claim that names its source
> is evidence of where it came from even when the source is not public, and removing them would
> leave the assertions here with no provenance at all.

> **For Claude:** REQUIRED SUB-SKILL: Use bitranox:process-agents-subagent-driven-development (recommended) or bitranox:process-plan-executor to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the design's contradictions, measure the five unmeasured premises, build the Python
baseline of graph A on scratch clones of two real repos, and decide adopt-versus-rebuild - everything the high plan
puts BEFORE the kernel is written.

**Architecture:** M0 edits documents under `workflow/design/`; S0 probes are standalone scripts
under `workflow/probes/` in the RESEARCH repo; M1 creates the `agentdag` repo from
`bitranox_template_py_cli` (bmk-managed, D7 pulled forward) and builds the graph-A baseline inside
its layers; D2 is a comparison document. D2 was decided REBUILD by the user 2026-08-17 (night), so
Tasks 8-18 build the M2 kernel hand-rolled per the mid plan, INSIDE the `agentdag` repo beside the
untouched M1 baseline (the baseline is the control M5 compares against; it is retired in a later
cleanup, not here). M3-M5 stay at mid-plan altitude until the M2 code exists (the mid plan's own
rule: detailed tasks only for the milestone about to start; a brief written now against code that
does not exist would rot).

**Tech Stack:** Python 3.12 (`uv`), `claude-agent-sdk` 0.2.139, `mcp` 1.29 (a sibling project's venv or a
scratch venv), `jsonschema`, `pyyaml`, `pydantic`, `rich-click`, `filelock`, `lib_cli_exit_tools`, git 2.4x, GNU make,
systemd 25x on the Linux dev host; Windows checks on the Windows dev host.

## Global Constraints

- Design source of truth is `workflow/staging/` (commit `ca92e60`) plus `workflow/design/REVIEW-2026-08-17.md`; the high and mid plans govern scope.
- ASCII only in every file; tables aligned with the plugin reformatter; commit messages via `-F` file, no AI attribution.
- No secret in argv, no secret in a committed file; probes read tokens from the environment set by the harness or from `~/.codex/auth.json` implicitly.
- Every probe writes its result to `workflow/design/probes/<name>.md` with the date, the command run, and the raw numbers.
- Every task ends with a commit on `main` of the RESEARCH repo (it is not a code repo yet; the worktree rule of the plan-writing skill does not apply until M2).
- Decided by the user 2026-08-17: D1 = option (a); the M1 baseline runs on SCRATCH CLONES only (no real repo is written); it is Python (structured, testable), using the best libraries for the job as the sibling projects do (not stdlib-only, not bash); it lives in the bmk-managed `agentdag` repo created from the template (D7 pulled forward), so `make test` is its gate and the template CI runs it on ubuntu/windows/macos; one hand run on the Windows dev host covers the SDK/env edge CI cannot.

---

### Task 1: M0 - apply the design fixes and re-snapshot

**Files:**
- Modify: `workflow/design/2026-08-17-agentdag-design.md`
- Modify: `workflow/design/schemas/*.json`, `workflow/design/schemas/tier-policy.example.yaml`
- Modify: `workflow/design/graphs/*.md`, `workflow/design/mcp-surface.md`
- Modify: `workflow/COORDINATOR-DESIGN.md` (C18-C20 only)
- Create: `workflow/staging-2/` (copy) and `workflow/staging-2/README.md`

**Interfaces:**
- Consumes: the fix list in `build-plan-mid.md` section M0 (one bullet per finding id).
- Produces: a design set with no open C-finding, validated schemas, a second snapshot named by commit.

**Out of scope** - do NOT touch, though they look related:
- `workflow/staging/` - the frozen review input; the review cites its line numbers.
- Anything under `memory/` or `harnesses/` - other areas of the research.

**STOP conditions** - stop and report rather than improvise, if:
- a fix in the mid plan's list would change a DECIDED item (tokens primary, option (c), (D) for resources, knowledge cut) - that is a new decision, not a fix;
- two findings demand contradictory fixes (report both, propose one);
- the schemas stop validating and the fix is not obvious after one attempt.

- [ ] **Step 1: Work through the M0 fix list top to bottom, one file at a time, ticking each finding id in a scratch checklist**

Keep a scratch file `/tmp/.../m0-checklist.txt` with one line per id from `build-plan-mid.md` M0
(C1..C25, M1-M9, M18-M20, M23-M25, O-items named there, E9, E11, T3/T4/T5/T6/T8/T13 bullets).
Mark each `done <file>` as it is applied. Do not batch across files blindly: read the section,
apply, move on.

- [ ] **Step 2: Validate the schemas against their examples**

Run:
```bash
<mcp-venv>/bin/python - <<'PY'
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
base = Path("<workspace>/projects/public/KI/RESEARCH/workflow/design/schemas")
schemas = {p.name: json.loads(p.read_text()) for p in sorted(base.glob("*.schema.json"))}
reg = Registry()
for name, s in schemas.items():
    reg = reg.with_resource(name, Resource.from_contents(s)).with_resource(s.get("$id", name), Resource.from_contents(s))
bad = 0
for name, s in schemas.items():
    Draft202012Validator.check_schema(s); v = Draft202012Validator(s, registry=reg)
    errs = [e.message for x in s.get("examples", []) for e in v.iter_errors(x)]; bad += len(errs)
    print(name, len(errs), "errors")
raise SystemExit(1 if bad else 0)
PY
```
Expected: every line `0 errors`, exit 0.

- [ ] **Step 3: Grep for the old text of each contradiction and require zero hits**

Run (each must print 0):
```bash
cd <workspace>/projects/public/KI/RESEARCH/workflow/design
grep -c "not yet authored" mcp-surface.md            # C22
grep -c "resource:bmk-tool-env" graphs/*.md | grep -v ":0" | wc -l   # C18
grep -c "cost_usd\` from the records" graphs/*.md | grep -v ":0" | wc -l  # C11
grep -c "build-time" mcp-surface.md                  # C21
grep -c "MEASURE-ME" 2026-08-17-agentdag-design.md   # C24 (thresholds now in 3.5)
```
Expected: 0 for each.

- [ ] **Step 4: Reformat tables, sweep tells, snapshot, commit**

Run:
```bash
B=<home>/.claude/plugins/cache/bitranox-skills/bitranox/5.206.2
cd <workspace>/projects/public/KI/RESEARCH
python3 $B/skills/docs-md-table-formatting/reformat_tables.py workflow/design/*.md workflow/design/graphs/*.md
python3 $B/hooks/strip_typographic_tells.py workflow/design/*.md workflow/design/graphs/*.md
mkdir -p workflow/staging-2 && cp -r workflow/design workflow/staging-2/design && cp workflow/COORDINATOR-DESIGN.md workflow/staging-2/
printf '%s\n' "# Staging snapshot 2 (after M0)" "" "Frozen copy taken $(date -u +%F) at commit $(git rev-parse --verify -q --short HEAD) of workflow/design/ and COORDINATOR-DESIGN.md, after the M0 fixes of build-plan-mid.md." > workflow/staging-2/README.md
git add -A && git status --porcelain
```
Expected: only `workflow/design/**`, `workflow/COORDINATOR-DESIGN.md`, `workflow/staging-2/**` staged.
Then write the commit message to a file (its own command) and `git commit -F` it.

---

### Task 2: S0 probe - dispatch cost of an isolated SDK child

**Files:**
- Create: `workflow/probes/probe_sdk_dispatch_cost.py`
- Create: `workflow/design/probes/s0-dispatch-cost.md`

**Interfaces:**
- Consumes: the scratch venv with `claude-agent-sdk` (`scratchpad/sdkprobe/.venv`; recreate with `uv venv` + `uv pip install claude-agent-sdk` if absent).
- Produces: `first_turn_input_tokens`, `total_in`, `total_out`, `cache_read`, `total_cost_usd` for three arms; the re-derived `min_node_minutes` per row.

**Out of scope:** the Codex executor; anything that writes into the run store (none exists).

**STOP conditions:** the SDK refuses to run headlessly (auth); a turn costs more than 100k tokens (abort, do not loop).

- [ ] **Step 1: Write the probe**

```python
"""S0 probe: what does ONE isolated SDK dispatch cost, in tokens, per arm.

Arms: A setting_sources=[] + plain system prompt (the agentdag executor shape);
      B setting_sources=["project"] + preset system prompt (CLAUDE.md loaded);
      C setting_sources=None + plain system prompt (whole user cascade loaded).
Each arm asks a one-line question with tools=[] and reads ResultMessage.model_usage.
"""

from __future__ import annotations
import asyncio, json, sys, tempfile
from pathlib import Path
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage

QUESTION = "Reply with the single word OK."


def arms(cwd: Path) -> dict[str, ClaudeAgentOptions]:
    common = dict(cwd=str(cwd), tools=[], max_turns=1, model="haiku", permission_mode="dontAsk", env={"CLAUDECODE": ""})
    return {
        "A_isolated": ClaudeAgentOptions(
            system_prompt="You are a probe. Answer tersely.", setting_sources=[], **common
        ),
        "B_project": ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code"}, setting_sources=["project"], **common
        ),
        "C_default": ClaudeAgentOptions(
            system_prompt="You are a probe. Answer tersely.", setting_sources=None, **common
        ),
    }


async def run_arm(name: str, opts: ClaudeAgentOptions) -> dict:
    per_turn: list[dict] = []
    result: dict = {}
    async with ClaudeSDKClient(options=opts) as client:
        await client.query(QUESTION)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage) and msg.usage:
                per_turn.append(dict(msg.usage))
            elif isinstance(msg, ResultMessage):
                result = {
                    "total_cost_usd": msg.total_cost_usd,
                    "num_turns": msg.num_turns,
                    "duration_ms": msg.duration_ms,
                    "usage": msg.usage,
                    "model_usage": msg.model_usage,
                }
    return {"arm": name, "per_turn_usage": per_turn, "result": result}


async def main() -> int:
    cwd = Path(tempfile.mkdtemp(prefix="s0-dispatch-"))
    (cwd / "CLAUDE.md").write_text("# probe project\n\nA one-line project file.\n")
    out = [await run_arm(n, o) for n, o in arms(cwd).items()]
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Run it and capture the numbers**

Run: `cd <workspace>/projects/public/KI/RESEARCH && <scratchpad>/sdkprobe/.venv/bin/python workflow/probes/probe_sdk_dispatch_cost.py > workflow/probes/probe_sdk_dispatch_cost.result.json`
(use the real scratch venv path; if it is gone, `uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python claude-agent-sdk` in the scratchpad first).
Expected: three JSON objects; arm A `model_usage.inputTokens` far below arm C's.

- [ ] **Step 3: Write the result note and re-derive min_node_minutes**

`workflow/design/probes/s0-dispatch-cost.md`: the three arms' input/output/cache-read tokens and cost;
the dispatch overhead = arm A's first-turn input tokens; per tier row, `min_node_minutes` = the node
size at which that overhead is under 10 percent of the node's tokens, using the row's typical
tokens-per-minute of work (state the assumption). If arm A is under 5k tokens, write in bold that the
60k figure in the design applies to in-session subagents only and that design 3.5's default changes.

- [ ] **Step 4: Commit** (message file first, then `git add workflow/probes/probe_sdk_dispatch_cost.py workflow/probes/probe_sdk_dispatch_cost.result.json workflow/design/probes/s0-dispatch-cost.md && git commit -F <file>`).

---

### Task 3: S0 probe - planner emission validity

**Files:**
- Create: `workflow/probes/probe_planner_validity.py`
- Create: `workflow/design/probes/s0-planner-validity.md`

**Interfaces:**
- Consumes: `workflow/design/schemas/node-spec.schema.json`; the SDK scratch venv; the scenario-B description in `workflow/design/graphs/B-root-cause.md`.
- Produces: rejection rate over 20 emissions (schema failures vs 2.4-rule failures), and the list of failing fields.

**STOP conditions:** more than 20 emissions attempted; any emission attempts to run a tool (tools=[] must hold).

- [ ] **Step 1: Write the probe**

```python
"""S0 probe: how often does a deep-row model emit a VALID node spec for the next node of graph B?"""

from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

ROOT = Path("<workspace>/projects/public/KI/RESEARCH/workflow/design")
SCHEMA = json.loads((ROOT / "schemas/node-spec.schema.json").read_text())
V = Draft202012Validator(SCHEMA)
ALLOWED_KINDS = {"work", "gate", "synth", "map", "reduce", "wait", "batch"}  # never apply/approve from a planner
BRIEF = (
    """You are the planner node of a root-cause debugging run (graph B). The last node was
`w_investigate` and its record says: status done, key_facts {hypothesis: "the DHCP client is
starved by a stimer storm", evidence_refs: 3, confidence: 0.6}. Emit the NEXT node spec as ONE
JSON object and nothing else, conforming to this JSON Schema (draft 2020-12):
"""
    + json.dumps(SCHEMA)
    + """
Constraints: kind must be one of work|gate|synth|map|reduce|wait|batch; write_set paths must
start with wt/; deadline_s <= 3600; budget.tokens must name only the row 'sonnet' or 'opus';
knowledge must be [] ; requires must be []; deps must be ["w_investigate"]."""
)


def rules(spec: dict) -> list[str]:
    errs = []
    if spec.get("kind") not in ALLOWED_KINDS:
        errs.append(f"kind {spec.get('kind')} not allowed for a planner")
    if any(not str(p).startswith("wt/") for p in spec.get("write_set", [])):
        errs.append("write_set outside wt/")
    if (spec.get("deadline_s") or 0) > 3600:
        errs.append("deadline over ceiling")
    rows = set((spec.get("budget") or {}).get("tokens", {}).keys())
    if not rows or not rows <= {"sonnet", "opus"}:
        errs.append(f"budget rows {rows}")
    if spec.get("deps") != ["w_investigate"]:
        errs.append("deps not the last node")
    return errs


async def one(client: ClaudeSDKClient) -> dict:
    await client.query(BRIEF)
    text = ""
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        elif isinstance(msg, ResultMessage):
            pass
    raw = text.strip().strip("`")
    if raw.startswith("json"):
        raw = raw[4:].strip()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"parse": f"invalid JSON: {e}", "schema": [], "rules": []}
    return {
        "parse": "ok",
        "schema": [e.message for e in V.iter_errors(spec)],
        "rules": rules(spec),
        "kind": spec.get("kind"),
    }


async def main() -> int:
    opts = ClaudeAgentOptions(
        system_prompt="You emit exactly one JSON object.",
        setting_sources=[],
        tools=[],
        max_turns=1,
        model="opus",
        permission_mode="dontAsk",
        env={"CLAUDECODE": ""},
    )
    results = []
    for i in range(20):
        async with ClaudeSDKClient(options=opts) as client:
            results.append(await one(client))
        print(i, results[-1]["parse"], len(results[-1]["schema"]), len(results[-1]["rules"]), file=sys.stderr)
    valid = sum(1 for r in results if r["parse"] == "ok" and not r["schema"] and not r["rules"])
    print(json.dumps({"n": len(results), "valid": valid, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Run it (20 opus calls; expect a few minutes and a few dollars-equivalent of tokens)**

Run: `<scratch venv>/bin/python workflow/probes/probe_planner_validity.py > workflow/probes/probe_planner_validity.result.json`
Expected: a JSON with `valid` out of 20 and per-emission failure lists.

- [ ] **Step 3: Write `s0-planner-validity.md`**: valid/20, the failing fields ranked, and the consequence per the high plan's risk 2 (over 90 percent valid: keep 2.4 as designed; 50-90: keep the one re-run; under 50: graph B waits).

- [ ] **Step 4: Commit.**

---

### Task 4: S0 probe - one paid Codex call and its rollout log

**Files:**
- Create: `workflow/probes/probe_codex_call.py`
- Create: `workflow/design/probes/s0-codex-rollout.md`

**Interfaces:**
- Consumes: `codex mcp-server` (codex-cli 0.144.5), the `mcp` package in a venv that has it, `~/.codex/auth.json` present.
- Produces: whether a rollout file exists for the returned `threadId`, its token fields, and the wall time.

**STOP conditions:** the call is refused for auth (record it, stop); the call runs longer than 3 minutes (kill, record).

- [ ] **Step 1: Write the probe** (one tiny call, `sandbox: read-only`, `approval-policy: never`, prompt "Reply with the single word OK and nothing else." - then search `~/.codex/sessions/**/rollout-*.jsonl` for the returned threadId and sum the token fields):

```python
from __future__ import annotations
import asyncio, glob, json, os, time
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


async def main() -> int:
    t0 = time.monotonic()
    params = StdioServerParameters(
        command="codex", args=["mcp-server"], env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(
                "codex",
                {
                    "prompt": "Reply with the single word OK and nothing else.",
                    "sandbox": "read-only",
                    "approval-policy": "never",
                    "cwd": "/tmp",
                },
            )
    dt = time.monotonic() - t0
    payload = res.structuredContent or {}
    thread = payload.get("threadId")
    hits = []
    for f in glob.glob(os.path.expanduser("~/.codex/sessions/**/rollout-*.jsonl"), recursive=True):
        text = open(f, errors="replace").read()
        if thread and thread in text:
            toks = {"input": 0, "output": 0, "reasoning": 0}
            for line in text.splitlines():
                for k, key in (
                    ("input", '"input_tokens":'),
                    ("output", '"output_tokens":'),
                    ("reasoning", '"reasoning_output_tokens":'),
                ):
                    i = line.find(key)
                    if i >= 0:
                        try:
                            toks[k] += int(line[i + len(key) :].split(",")[0].split("}")[0])
                        except ValueError:
                            pass
            hits.append({"file": f, "tokens": toks})
    print(
        json.dumps(
            {
                "seconds": round(dt, 1),
                "threadId": thread,
                "content": payload.get("content"),
                "rollout_hits": hits,
                "raw_is_error": res.isError,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Run** with that venv's python; save to `workflow/probes/probe_codex_call.result.json` (contains no secret: threadId and OK).
- [ ] **Step 3: Write `s0-codex-rollout.md`**: found/not found, tokens, seconds; consequence: reconciliation viable (M4 keeps the second journal line) or Codex nodes stay charged in full (design 6 text updated in M0 or a follow-up).
- [ ] **Step 4: Commit.**

---

### Task 5: S0 probes - mcp per-tool scopes, and scopes/users on the Linux dev host

**Files:**
- Create: `workflow/design/probes/s0-mcp-scopes.md`
- Create: `workflow/design/probes/s0-systemd-scopes.md`

- [ ] **Step 1: mcp scopes** - read, in that venv, `mcp/server/auth/middleware/bearer_auth.py`, `mcp/server/fastmcp/server.py` (the `required_scopes` path) and `mcp/server/auth/middleware/auth_context.py`; answer in the note: is there a per-tool scope hook in 1.29? If not, quote the `get_access_token()` accessor tool code would use, and record that L1's server checks `run:approve` inside the tool. Command: `grep -n "required_scopes\|get_access_token\|scopes" <venv>/lib/python3.14/site-packages/mcp/server/auth/middleware/*.py <venv>/lib/python3.14/site-packages/mcp/server/fastmcp/server.py`.
- [ ] **Step 2: systemd scopes** - as `<user>` on the Linux dev host: `systemd-run --user --scope --unit=agentdag-probe sleep 30 & sleep 1; systemctl --user status agentdag-probe.scope | head -5; cat /proc/$(pgrep -x sleep | head -1)/cgroup`; then `loginctl show-user <user> -p Linger`; then check whether a second user exists or can be created without root (`id agentdag 2>&1`, `sudo -n true && echo NOPASSWD`). Record: can a scope be created; does the cgroup path name it; is linger on; is there a NOPASSWD path to create a service user (D4).
- [ ] **Step 3: Commit both notes.**

---

### Task 6a: M1 - create the `agentdag` repo from the template (D7 pulled forward)

**Files:**
- Create: `<workspace>/projects/public/KI/agentdag/` (copy of `public/apps/bitranox_template_py_cli`, renamed)
- Modify: `agentdag/pyproject.toml` (name, description, keywords, `requires-python`, deps, `[tool.ci]` choco make)
- Modify: `agentdag/README.md`, `agentdag/CHANGELOG.md`, `agentdag/CLAUDE.md` (project section only)

**Interfaces:**
- Consumes: the template at `<workspace>/projects/public/apps/bitranox_template_py_cli` (v1.7.1, bmk 3.14.0), `uv`, `bmk` via `make`.
- Produces: a bmk-managed package `agentdag` with the template's layers (`domain/`, `application/`, `adapters/`, `composition/`), `make test` green on the pristine scaffold, a local git repo on `main` with one commit. Later tasks import `agentdag.domain`, `agentdag.application.ports`, register CLI commands through `adapters/cli/root.py::_register_commands` and use `adapters/cli/typed_click.py`'s `option`/`argument` facade (pyright-strict rich-click).

**Out of scope:** the GitHub repo and CI - creating a public repo is an outward action; STOP after the local commit and ask the user for the go (`gh repo create bitranox/agentdag --public`), then push and watch CI by head sha + workflow name. `.github/` is template-managed: never edit it.

**STOP conditions:** `rename.sh` fails or leaves the old name anywhere (`grep -rn bitranox_template_py_cli` must be empty outside `CHANGELOG.md` history); `make test` is red on the pristine scaffold (that is a template or tool-env problem, not ours - report it); the tree rule "new repos use main" would be violated.

- [ ] **Step 1: Copy, rename, reset history**

```bash
cd <workspace>/projects/public/KI
cp -r ../apps/bitranox_template_py_cli agentdag && cd agentdag && rm -rf .git .venv .idea
git init -q -b main && git config core.fileMode false
./rename.sh                     # uvx rename-project --yes: derives the new name from the directory (agentdag)
grep -rn "bitranox_template_py_cli\|bitranox-template-py-cli" --exclude-dir=.git . | grep -v CHANGELOG.md | wc -l    # expect 0
```

- [ ] **Step 2: Set the project fields**

In `pyproject.toml`: `description = "Coordinator that runs a small graph of AI-agent nodes with bounded spend, mechanical gates and journaled state"`, `keywords = ["agents", "coordinator", "dag", "claude", "codex"]`, `requires-python = ">=3.12"` (drop the 3.10/3.11 classifiers; reason: the kernel will use 3.11+ asyncio and Self, and fewer CI cells), add `"claude-agent-sdk>=0.2.139"` and `"filelock>=3.16"` to `dependencies`, and `system-dependencies-windows-choco = ["make"]` under `[tool.ci]` so the Windows runner has GNU make for the integration gate test. Set `version = "0.1.0"`. Rewrite `README.md`'s first paragraph and `CHANGELOG.md` to a single `0.1.0 - scaffold` entry.

- [ ] **Step 3: Gate the pristine scaffold**

Run: `cd <workspace>/projects/public/KI/agentdag && uv venv -q .venv && uv pip install -q -e ".[dev]" --python .venv/bin/python && env -u VIRTUAL_ENV BMK_PYTHON_CMD=$PWD/.venv/bin/python make test > /tmp/agentdag-scaffold-test.log 2>&1; echo RC=$? >> /tmp/agentdag-scaffold-test.log; tail -3 /tmp/agentdag-scaffold-test.log`
Expected: `RC=0`. Read the RC from the log, not the pipeline exit.

- [ ] **Step 4: Commit locally, then STOP for the GitHub decision**

Write the message file in its own command, then `git add -A && git commit -q -F <file>`, `git log --oneline -1`. Report: repo path, gate result, and the one question (create `bitranox/agentdag` public on GitHub now, MIT, `main` protected?).

---

### Task 6b: M1 - graph A baseline inside `agentdag`, on scratch clones

**Files:**
- Create: `src/agentdag/domain/graph_a.py` (records + pure functions)
- Create: `src/agentdag/application/graph_a_ports.py` (the ports the graph needs)
- Create: `src/agentdag/application/graph_a.py` (the graph as code)
- Create: `src/agentdag/adapters/graph_a/{__init__,git_cli,gate_make,store_fs,work_claude_sdk,approve_console}.py`
- Create: `src/agentdag/adapters/cli/commands/graph_a.py`; Modify: `src/agentdag/adapters/cli/root.py` (register the group)
- Create: `src/agentdag/composition/graph_a.py`
- Test: `tests/test_graph_a_domain.py`, `tests/test_graph_a_adapters.py`, `tests/test_graph_a_run.py`
- Create: `workflow/design/probes/m1-baseline.md` (in RESEARCH)

**Interfaces:**
- Consumes: Task 6a's package; two REAL repos of a fleet chore as the READ-ONLY source of the scratch clones; a `BRIEF.md`.
- Produces: the CLI `agentdag graph-a scratch REAL_REPOS.txt --scratch DIR` and `agentdag graph-a run REPOS.txt BRIEF.md --scratch DIR [--parallel 2 --model sonnet --runs DIR --lock FILE]`; records `WorkResult`, `Tally`, `TallySummary`, `PushIntent`; ports `GitPort`, `GatePort`, `WorkPort`, `ApprovePort`, `RunStore`; `run_graph(...) -> int`.
- Layer contract (import-linter, already in pyproject): domain imports nothing of ours; application imports domain; adapters import application + domain; composition wires. `pydantic` in the domain is allowed (the contract governs internal imports only).

**Out of scope** - do NOT touch, though they look related:
- The REAL repos: read once by `git clone --mirror`, never written; `apply` refuses any target outside `<scratch>/origin/`.
- A journal, a token cap, an unattended approve, Codex, the run store schema - M2-M4; the baseline must LACK them so D2 can price them.
- The template's demo commands (`hello`, `send-email`, `logdemo`): leave them; removing them is a separate cleanup task.

**STOP conditions** - stop and report rather than improvise, if:
- a push target resolves outside `<scratch>/origin/`; if you find yourself loosening `_assert_scratch_target`, stop;
- `make test` is red on a scratch clone BEFORE the change (baseline red - report, do not migrate);
- the SDK cannot edit files under `permission_mode="acceptEdits"` (report the refusal; never `bypassPermissions`);
- pyright strict or import-linter push you toward moving I/O into `domain/` - stop, the split below is the intended one;
- the gate to green takes more than half a day - report what is red and why.

- [ ] **Step 1: RED - the domain tests**

`tests/test_graph_a_domain.py`:
```python
from pathlib import Path
from agentdag.domain.graph_a import (
    PushIntent,
    Tally,
    dedup_key,
    is_scratch_target,
    parse_repos_text,
    reduce_tally,
    stage,
)


def test_parse_repos_text_skips_blank_and_comment_lines() -> None:
    assert parse_repos_text("/a/one\n\n# c\n  /b/two  \n") == [Path("/a/one"), Path("/b/two")]


def test_reduce_tally_counts_and_stage_keeps_only_passed() -> None:
    rows = [
        Tally(repo=Path("/o/a.git"), status="passed", head_sha="a" * 40, test_rc=0),
        Tally(repo=Path("/o/b.git"), status="failed", head_sha="b" * 40, test_rc=2),
        Tally(repo=Path("/o/c.git"), status="work-failed", head_sha="c" * 40, test_rc=None),
    ]
    summary = reduce_tally(rows)
    assert (summary.passed, summary.failed) == (1, 2)
    assert stage(summary) == [PushIntent(repo=Path("/o/a.git"), head_sha="a" * 40, dedup_key="a.git-" + "a" * 40)]


def test_dedup_key_is_name_and_sha() -> None:
    assert dedup_key(Path("/x/repo.git"), "f" * 40) == "repo.git-" + "f" * 40


def test_is_scratch_target(tmp_path: Path) -> None:
    (tmp_path / "origin").mkdir()
    assert is_scratch_target(tmp_path / "origin" / "r.git", tmp_path)
    assert not is_scratch_target(Path("<workspace>/projects/public/libs/x"), tmp_path)
```
Run: `.venv/bin/python -m pytest tests/test_graph_a_domain.py -q` -> ImportError. 

- [ ] **Step 2: GREEN - `src/agentdag/domain/graph_a.py`**

```python
"""Graph A (fleet migration) records and pure functions. No I/O here."""

from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import BaseModel

__all__ = [
    "WorkResult",
    "Tally",
    "TallySummary",
    "PushIntent",
    "parse_repos_text",
    "reduce_tally",
    "stage",
    "dedup_key",
    "is_scratch_target",
]


class WorkResult(BaseModel):
    """What one work node reported: typed, never prose."""

    ok: bool
    num_turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    error: str | None = None


class Tally(BaseModel):
    repo: Path
    status: Literal["passed", "failed", "work-failed"]
    head_sha: str
    test_rc: int | None
    work: WorkResult | None = None


class TallySummary(BaseModel):
    passed: int
    failed: int
    rows: list[Tally]


class PushIntent(BaseModel):
    repo: Path
    head_sha: str
    dedup_key: str


def parse_repos_text(text: str) -> list[Path]:
    """One absolute path per line; blank lines and '#' comments ignored."""
    return [Path(line.strip()) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def dedup_key(repo: Path, head_sha: str) -> str:
    return f"{repo.name}-{head_sha}"


def reduce_tally(rows: list[Tally]) -> TallySummary:
    passed = sum(1 for r in rows if r.status == "passed")
    return TallySummary(passed=passed, failed=len(rows) - passed, rows=rows)


def stage(summary: TallySummary) -> list[PushIntent]:
    """One push intent per passed repo; the dedup key is (repo name, head sha)."""
    return [
        PushIntent(repo=r.repo, head_sha=r.head_sha, dedup_key=dedup_key(r.repo, r.head_sha))
        for r in summary.rows
        if r.status == "passed"
    ]


def is_scratch_target(repo: Path, scratch_root: Path) -> bool:
    """A push may only go to a bare clone under <scratch>/origin - never a real repo."""
    return repo.resolve().is_relative_to((scratch_root / "origin").resolve())
```
Run the domain tests -> 4 passed. Commit (`git add src/agentdag/domain/graph_a.py tests/test_graph_a_domain.py`, message file, `-F`).

- [ ] **Step 3: The ports - `src/agentdag/application/graph_a_ports.py`**

```python
"""Ports graph A needs. Adapters implement them; tests inject fakes at these seams."""

from __future__ import annotations
from pathlib import Path
from typing import Protocol
from agentdag.domain.graph_a import WorkResult

__all__ = ["GitPort", "GatePort", "WorkPort", "ApprovePort", "RunStore"]


class GitPort(Protocol):
    def mirror(self, source: Path, dest: Path) -> None: ...
    def clone(self, origin: Path, dest: Path) -> None: ...
    def head_sha(self, repo: Path) -> str: ...
    def has_commit(self, repo: Path, sha: str) -> bool: ...
    def default_branch(self, bare_repo: Path) -> str: ...
    def push(self, worktree: Path, target: Path, branch: str) -> None: ...


class GatePort(Protocol):
    def run(self, worktree: Path, log: Path) -> int:
        """Run the gate under the host-wide lock; return its exit code."""
        ...


class WorkPort(Protocol):
    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult: ...


class ApprovePort(Protocol):
    def confirm(self, prompt: str) -> bool: ...


class RunStore(Protocol):
    root: Path

    def worktree(self, name: str) -> Path: ...
    def log(self, name: str) -> Path: ...
    def home(self, name: str) -> Path: ...
    def write_json(self, rel: str, text: str) -> None: ...
    def marker(self, key: str) -> Path: ...
```

- [ ] **Step 4: RED - the graph test with fakes at the ports (`tests/test_graph_a_run.py`)**

The work port is the ONE external edge (Claude); everything else is the real adapter over real temp git repos. The fake work commits a file, exactly what a good agent does.
```python
import asyncio, subprocess
from pathlib import Path
import pytest
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.graph_a.store_fs import FsRunStore
from agentdag.application.graph_a import apply, run_graph
from agentdag.domain.graph_a import PushIntent, WorkResult


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout.strip()


def make_repo(root: Path, name: str, makefile: str) -> Path:
    repo = root / name
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@example.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "Makefile").write_text(makefile)
    (repo / "README.md").write_text(f"# {name}\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    return repo


GREEN = "test:\n\t@exit 0\n"


class CommittingWork:
    """Stands in for the Claude node: edits a file and commits, like the brief asks."""

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        (worktree / "CHANGELOG.md").write_text(brief + "\n")
        git("add", "-A", cwd=worktree)
        git("commit", "-q", "-m", "baseline change", cwd=worktree)
        return WorkResult(ok=True, num_turns=1, input_tokens=10, output_tokens=5, cost_usd=0.0)


class YesApprover:
    def confirm(self, prompt: str) -> bool:
        return True


def true_gate(tmp_path: Path) -> MakeTestGate:
    # the gate is a subprocess exit code; use the interpreter so the unit test needs no `make`
    import sys

    return MakeTestGate(lock=tmp_path / "gate.lock", command=(sys.executable, "-c", "raise SystemExit(0)"))


def test_run_graph_end_to_end_pushes_after_approve(tmp_path: Path) -> None:
    gitp = GitCli()
    real = make_repo(tmp_path, "real1", GREEN)
    scratch = tmp_path / "scratch"
    origin = scratch / "origin" / "real1.git"
    origin.parent.mkdir(parents=True)
    gitp.mirror(real, origin)
    store = FsRunStore.create(tmp_path / "runs")
    rc = asyncio.run(
        run_graph(
            origins=[origin],
            brief="add a line",
            model="sonnet",
            parallel=2,
            scratch_root=scratch,
            git=gitp,
            gate=true_gate(tmp_path),
            work=CommittingWork(),
            approve=YesApprover(),
            store=store,
        )
    )
    assert rc == 0
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "HEAD", cwd=store.worktree("real1"))
    assert git("rev-parse", "main", cwd=real) != git("rev-parse", "main", cwd=origin)  # the REAL repo is untouched
    assert (store.root / "tally.json").exists()


def test_apply_replay_pushes_nothing_and_refuses_non_scratch(tmp_path: Path) -> None:
    gitp = GitCli()
    real = make_repo(tmp_path, "p", GREEN)
    scratch = tmp_path / "s"
    origin = scratch / "origin" / "p.git"
    origin.parent.mkdir(parents=True)
    gitp.mirror(real, origin)
    store = FsRunStore.create(tmp_path / "runs")
    wt = store.worktree("p")
    gitp.clone(origin, wt)
    (wt / "x").write_text("x")
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m", "c", cwd=wt)
    sha = gitp.head_sha(wt)
    intents = [PushIntent(repo=origin, head_sha=sha, dedup_key=f"p.git-{sha}")]
    assert apply(intents, scratch_root=scratch, git=gitp, store=store) == ["pushed"]
    assert apply(intents, scratch_root=scratch, git=gitp, store=store) == ["already-done"]
    with pytest.raises(ValueError, match="not under"):
        apply(
            [PushIntent(repo=Path("<workspace>/projects/public/libs/x"), head_sha="0" * 40, dedup_key="x-0")],
            scratch_root=scratch,
            git=gitp,
            store=store,
        )
```
Run: `.venv/bin/python -m pytest tests/test_graph_a_run.py -q` -> ImportError.

- [ ] **Step 5: GREEN - the graph as code, `src/agentdag/application/graph_a.py`**

```python
"""Graph A as code: discover -> map(worktree, work, gate) -> tally -> stage -> approve -> apply.
Branches ONLY on typed records. Deliberately no journal, no token cap, no unattended approve."""

from __future__ import annotations
import asyncio
from pathlib import Path
from agentdag.application.graph_a_ports import ApprovePort, GatePort, GitPort, RunStore, WorkPort
from agentdag.domain.graph_a import PushIntent, Tally, dedup_key, is_scratch_target, reduce_tally, stage

__all__ = ["run_graph", "apply", "make_scratch_fleet"]


def make_scratch_fleet(real_repos: list[Path], scratch: Path, git: GitPort) -> list[Path]:
    """Bare mirror per real repo under <scratch>/origin/ - the only push targets."""
    origin = scratch / "origin"
    origin.mkdir(parents=True, exist_ok=True)
    targets: list[Path] = []
    for repo in real_repos:
        dest = origin / (repo.name + ".git")
        if not dest.exists():
            git.mirror(repo, dest)
        targets.append(dest)
    return targets


def apply(intents: list[PushIntent], *, scratch_root: Path, git: GitPort, store: RunStore) -> list[str]:
    """Idempotent: done marker per key; external-state check (sha already in origin) before pushing."""
    outcomes: list[str] = []
    for it in intents:
        if not is_scratch_target(it.repo, scratch_root):
            raise ValueError(f"{it.repo} is not under {scratch_root}/origin - a real repo is never a push target")
        marker = store.marker(it.dedup_key)
        if marker.exists():
            outcomes.append("already-done")
            continue
        if git.has_commit(it.repo, it.head_sha):
            outcomes.append("already-present")
        else:
            git.push(store.worktree(it.repo.name.removesuffix(".git")), it.repo, git.default_branch(it.repo))
            outcomes.append("pushed")
        marker.touch()
    return outcomes


async def run_graph(
    *,
    origins: list[Path],
    brief: str,
    model: str,
    parallel: int,
    scratch_root: Path,
    git: GitPort,
    gate: GatePort,
    work: WorkPort,
    approve: ApprovePort,
    store: RunStore,
) -> int:
    if not origins:
        return 0  # g_discover halts
    sem = asyncio.Semaphore(parallel)

    async def branch(origin: Path) -> Tally:  # m_migrate@i
        name = origin.name.removesuffix(".git")
        wt = store.worktree(name)
        async with sem:
            await asyncio.to_thread(git.clone, origin, wt)
            wr = await work.run(wt, brief, model, store.home(name))
            if not wr.ok:
                t = Tally(repo=origin, status="work-failed", head_sha=git.head_sha(wt), test_rc=None, work=wr)
            else:
                rc = await asyncio.to_thread(
                    gate.run, wt, store.log(f"{name}.test.log")
                )  # g_test@i, serialised by the lock
                t = Tally(
                    repo=origin,
                    status="passed" if rc == 0 else "failed",
                    head_sha=git.head_sha(wt),
                    test_rc=rc,
                    work=wr,
                )
        store.write_json(f"tally/{name}.json", t.model_dump_json(indent=1))
        return t

    rows = list(await asyncio.gather(*(branch(o) for o in origins)))
    summary = reduce_tally(rows)
    store.write_json("tally.json", summary.model_dump_json(indent=1))  # r_tally
    intents = stage(summary)  # s_push_intent
    for it in intents:
        store.write_json(f"intents/{it.dedup_key}.json", it.model_dump_json())
    if not intents:
        return 0  # rt_pushable
    listing = "\n".join(f"  {it.repo}  {it.head_sha[:12]}" for it in intents)
    if not approve.confirm(
        f"passed {summary.passed} failed {summary.failed}; push list:\n{listing}\napprove pushing?"
    ):  # a_push_list
        return 0
    apply(intents, scratch_root=scratch_root, git=git, store=store)  # ap_push
    return 0
```
`dedup_key` is imported for the CLI's listing later; if ruff flags it unused here, drop the import.

- [ ] **Step 6: GREEN - the adapters (`src/agentdag/adapters/graph_a/`)**

`git_cli.py`:
```python
"""GitPort over the git CLI (subprocess, utf-8, errors replaced)."""

from __future__ import annotations
import subprocess
from pathlib import Path

__all__ = ["GitCli"]


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, encoding="utf-8", errors="replace")


class GitCli:
    def mirror(self, source: Path, dest: Path) -> None:
        _git("clone", "-q", "--mirror", str(source), str(dest))

    def clone(self, origin: Path, dest: Path) -> None:
        _git("-c", "core.fileMode=false", "clone", "-q", str(origin), str(dest))
        _git("config", "user.email", "agentdag@localhost", cwd=dest)
        _git("config", "user.name", "agentdag", cwd=dest)

    def head_sha(self, repo: Path) -> str:
        return _git("rev-parse", "--verify", "-q", "HEAD", cwd=repo).stdout.strip()

    def has_commit(self, repo: Path, sha: str) -> bool:
        return _git("cat-file", "-e", f"{sha}^{{commit}}", cwd=repo, check=False).returncode == 0

    def default_branch(self, bare_repo: Path) -> str:
        return _git("symbolic-ref", "--short", "HEAD", cwd=bare_repo).stdout.strip()

    def push(self, worktree: Path, target: Path, branch: str) -> None:
        _git("push", "-q", str(target), f"HEAD:{branch}", cwd=worktree)
```
`gate_make.py`:
```python
"""GatePort: `make test` (or an injected command) under ONE host-wide file lock - the bmk tool env is shared."""

from __future__ import annotations
import subprocess
from collections.abc import Sequence
from pathlib import Path
from filelock import FileLock

__all__ = ["MakeTestGate"]


class MakeTestGate:
    def __init__(self, *, lock: Path, command: Sequence[str] = ("make", "test")) -> None:
        self._lock = lock
        self._command = tuple(command)

    def run(self, worktree: Path, log: Path) -> int:
        with FileLock(str(self._lock)):
            proc = subprocess.run(
                list(self._command), cwd=worktree, capture_output=True, encoding="utf-8", errors="replace"
            )
        log.write_text(proc.stdout + proc.stderr)
        return proc.returncode
```
`store_fs.py`:
```python
"""RunStore on the filesystem: <base>/<utc-stamp>/{wt,tally,intents,done,log,home}."""

from __future__ import annotations
import time
from pathlib import Path

__all__ = ["FsRunStore"]


class FsRunStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def create(cls, base: Path) -> "FsRunStore":
        root = base / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        for d in ("wt", "tally", "intents", "done", "log", "home"):
            (root / d).mkdir(parents=True, exist_ok=True)
        return cls(root)

    def worktree(self, name: str) -> Path:
        return self.root / "wt" / name

    def log(self, name: str) -> Path:
        return self.root / "log" / name

    def home(self, name: str) -> Path:
        p = self.root / "home" / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write_json(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def marker(self, key: str) -> Path:
        return self.root / "done" / key
```
`work_claude_sdk.py` (the external edge; not unit-tested, covered by the attended run):
```python
"""WorkPort over the Claude Agent SDK: one isolated client per node, project settings excluded."""

from __future__ import annotations
from pathlib import Path
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import ResultMessage
from agentdag.domain.graph_a import WorkResult

__all__ = ["ClaudeSdkWork"]
_PROMPT = (
    "Apply the change described in your system prompt to this repository. Commit with a clear message. Do not push."
)


class ClaudeSdkWork:
    def __init__(self, *, max_turns: int = 25) -> None:
        self._max_turns = max_turns

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        opts = ClaudeAgentOptions(
            cwd=str(worktree),
            system_prompt=brief,
            setting_sources=[],
            model=model,
            max_turns=self._max_turns,
            permission_mode="acceptEdits",
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
            env={"HOME": str(home), "CLAUDECODE": ""},
        )
        try:
            async with ClaudeSDKClient(options=opts) as client:
                await client.query(_PROMPT)
                async for msg in client.receive_response():
                    if isinstance(msg, ResultMessage):
                        usage = msg.usage or {}
                        return WorkResult(
                            ok=not msg.is_error,
                            num_turns=msg.num_turns,
                            input_tokens=int(usage.get("input_tokens", 0)),
                            output_tokens=int(usage.get("output_tokens", 0)),
                            cost_usd=msg.total_cost_usd,
                        )
        except Exception as exc:  # noqa: BLE001 - the external edge: report as a failed node, never past the branch
            return WorkResult(ok=False, error=repr(exc))
        return WorkResult(ok=False, error="no ResultMessage")
```
`approve_console.py`:
```python
"""ApprovePort on the console - attended on purpose; the unattended approve is M3's."""

from __future__ import annotations
import rich_click as click

__all__ = ["ConsoleApprove"]


class ConsoleApprove:
    def confirm(self, prompt: str) -> bool:
        click.echo(prompt)
        return click.confirm("approve?", default=False)
```
`__init__.py` re-exports the five classes with `__all__`.

- [ ] **Step 7: Adapter tests over real temp git repos (`tests/test_graph_a_adapters.py`)**

```python
import subprocess, sys
from pathlib import Path
import pytest
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.graph_a.store_fs import FsRunStore


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout.strip()


def make_repo(root: Path, name: str, makefile: str) -> Path:
    repo = root / name
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@example.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "Makefile").write_text(makefile)
    (repo / "README.md").write_text(f"# {name}\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    return repo


def test_git_cli_mirror_clone_head_and_default_branch(tmp_path: Path) -> None:
    g = GitCli()
    real = make_repo(tmp_path, "r", "test:\n\t@exit 0\n")
    bare = tmp_path / "r.git"
    g.mirror(real, bare)
    wt = tmp_path / "wt"
    g.clone(bare, wt)
    assert g.head_sha(wt) == git("rev-parse", "HEAD", cwd=real)
    assert g.default_branch(bare) == "main" and g.has_commit(bare, g.head_sha(wt)) and not g.has_commit(bare, "0" * 40)


def test_gate_returns_the_command_exit_code_under_the_lock(tmp_path: Path) -> None:
    for code in (0, 1, 3):
        gate = MakeTestGate(lock=tmp_path / "l", command=(sys.executable, "-c", f"raise SystemExit({code})"))
        assert gate.run(tmp_path, tmp_path / f"g{code}.log") == code


@pytest.mark.integration
def test_gate_runs_real_make_test(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "m", "test:\n\t@exit 1\n")
    assert MakeTestGate(lock=tmp_path / "l").run(repo, tmp_path / "m.log") == 1


def test_store_layout(tmp_path: Path) -> None:
    s = FsRunStore.create(tmp_path / "runs")
    assert all((s.root / d).is_dir() for d in ("wt", "tally", "intents", "done", "log", "home"))
    s.write_json("tally/x.json", "{}")
    assert (s.root / "tally/x.json").read_text() == "{}"
    assert s.marker("k") == s.root / "done" / "k"
```
Run all three test files -> green.

- [ ] **Step 8: CLI + composition**

`src/agentdag/composition/graph_a.py`:
```python
"""Production wiring for graph A."""

from __future__ import annotations
from pathlib import Path
from agentdag.adapters.graph_a import ClaudeSdkWork, ConsoleApprove, FsRunStore, GitCli, MakeTestGate

__all__ = ["wire"]


def wire(*, runs: Path, lock: Path) -> dict[str, object]:
    return {
        "git": GitCli(),
        "gate": MakeTestGate(lock=lock),
        "work": ClaudeSdkWork(),
        "approve": ConsoleApprove(),
        "store": FsRunStore.create(runs),
    }
```
(pyright strict will want a typed structure rather than `dict[str, object]`: use a small `@dataclass(frozen=True) class GraphAWiring` with the five typed fields instead - do that.)

`src/agentdag/adapters/cli/commands/graph_a.py`: a `graph-a` group with `scratch` (REAL_REPOS_FILE, `--scratch`) and `run` (REPOS_FILE, BRIEF_FILE, `--scratch`, `--runs` default `<tmp>/agentdag-baseline`, `--parallel 2`, `--model sonnet`, `--lock` default `<tmp>/agentdag-bmk-tool-env.lock`), using the template's `typed_click` facade for `option`/`argument`, reading files with `Path.read_text()`, calling `parse_repos_text`, `make_scratch_fleet` (prints the written REPOS.txt path) and `asyncio.run(run_graph(...))` with `wire(...)`; exit codes through `lib_cli_exit_tools` as the other commands do. Register the group in `root.py::_register_commands`.

- [ ] **Step 9: The full gate, on Linux**

Run: `env -u VIRTUAL_ENV BMK_PYTHON_CMD=$PWD/.venv/bin/python make test > /tmp/agentdag-m1-test.log 2>&1; echo RC=$? >> /tmp/agentdag-m1-test.log; tail -5 /tmp/agentdag-m1-test.log`
Expected `RC=0` (ruff, pyright strict, import-linter, pytest incl. doctests, bandit, pip-audit). Fix what is red at the root; never loosen the gate.

- [ ] **Step 10: The scratch fleet from two real repos, dry-run with a trivial brief, then the real brief - attended**

```bash
printf '%s\n' <workspace>/projects/public/libs/<repo1> <workspace>/projects/public/libs/<repo2> > /tmp/agentdag-real.txt
.venv/bin/agentdag graph-a scratch /tmp/agentdag-real.txt --scratch /tmp/agentdag-scratch
printf '%s\n' "Add one line 'baseline probe' at the end of CHANGELOG.md. Change nothing else." > /tmp/agentdag-brief.md
.venv/bin/agentdag graph-a run /tmp/agentdag-scratch/REPOS.txt /tmp/agentdag-brief.md --scratch /tmp/agentdag-scratch --parallel 2
```
Expected: two worktrees, two tallies with tokens filled, ONE approve prompt; after `y` both scratch origins carry the new sha; both REAL repos unchanged (`git status --porcelain` empty, `git log -1` unchanged). Then the same with the real fleet-chore brief; time it; count interactions.

- [ ] **Step 11: Windows - one hand run on the Windows dev host for the SDK/env edge (CI covers the unit tests on windows-latest once the repo is on GitHub)**

`tar czf` the repo without `.venv`, scp with the fleet key (BatchMode), then on the box: `uv venv .venv-win && uv pip install -e ".[dev]" --python .venv-win\Scripts\python.exe && .venv-win\Scripts\python.exe -m pytest tests/test_graph_a_domain.py tests/test_graph_a_adapters.py tests/test_graph_a_run.py -q`. Expected: green. Check, not assume: the SDK's `env` option MERGES with the process environment on Windows (a replaced environment loses SystemRoot and the child dies silently) - if it replaces, drop the `HOME` override on `sys.platform == "win32"`. Record the result.

- [ ] **Step 12: Write `workflow/design/probes/m1-baseline.md`** (in RESEARCH): wall time, interactions, tokens per branch, the Windows result, what broke, and the properties the baseline lacks named from experience - at least replay after a crash mid-run, a token cap, an unattended approve. This is D2's input.

- [ ] **Step 13: Commit** in agentdag (message file, `-F`); commit the note in RESEARCH.

---

### Task 7: D2 - adopt versus rebuild

**Files:**
- Create: `workflow/design/D2-adopt-vs-rebuild.md`
- Modify: `DECISIONS.md` (a "Decided" line)

- [ ] **Step 1: For each of replay, token cap, approve, and for the T3/T4 operational requirements, fill a table**: hand-rolled kernel (M2-M3 as in the mid plan) vs DBOS (Python SDK: `@DBOS.workflow`/`@DBOS.step`, Postgres-checkpointed, exactly-once step results, durable sleep for approve) vs Temporal (server + worker, replay determinism rules): how each meets the section-9 negative tests, what runs on the Linux dev host (Postgres? a Temporal server?), how the single-writer lock, scopes and secrets are handled, and how the two-tier journal claim (in-session Workflow tool) survives. Read DBOS's docs first-hand (WebFetch), tag RELAYED where not. Price each of the three properties against the M1 baseline note (`m1-baseline.md`), which is D2's input.
- [ ] **Step 2: Decide and record** in DECISIONS.md; if adopt, the M2 detailed tasks are written for that substrate; if rebuild, for the mid plan's package layout.
- [ ] **Step 3: Commit.**

---

## M2 - the kernel (Tasks 8-18), decided REBUILD

Written 2026-08-17 night against the design (`RESEARCH/workflow/design/2026-08-17-agentdag-design.md` sections
2-4, 7, 9), the mid plan's M2 section, the M1 code as merged on `agentdag` `main` (`478c579`) and
the M2 input list in `DECISIONS.md` item 6. Every task runs in the `agentdag` repo on ONE feature
branch `feat/kernel` off `main` (bmk gate per task, PR at the end as M1 did), except Task 8 and
Task 18's note, which live in RESEARCH.

**M2 constraints, on top of the global ones**

- The M1 baseline modules (`domain/graph_a.py`, `application/graph_a.py`, `application/graph_a_ports.py`,
  `adapters/graph_a/*`, `adapters/cli/commands/graph_a.py`, `composition/graph_a.py`, their
  tests) are NOT rewritten in M2. The kernel REUSES `GitPort`/`GitCli`, `GatePort`/`MakeTestGate`
  and the pure functions of `domain/graph_a.py`; it does not import `application/graph_a.py`. Two
  adjacent fixes are allowed and named where they happen (Task 11: git stderr in the error; Task 13:
  a timeout and message on the gate's `FileLock`).
- Package layout (mid plan M2, adjusted to the repo as it is): domain `domain/models.py`,
  `domain/journal.py`, `domain/keys.py`, `domain/policy.py`, additions to `domain/errors.py`;
  application `application/kernel/{ports,replay,dispatch,context,workflow_check,run}.py`,
  `application/workflows/{__init__,graph_a}.py`; adapters `adapters/kernel/{journal_jsonl,lock_file,clock_utc,run_store_fs,isolation_scan,executor_claude,policy_yaml,scope_systemd,scope_none}.py`,
  `adapters/cli/commands/run.py`; composition `composition/kernel.py`; package data
  `src/agentdag/schemas/*.schema.json` (copied from `workflow/design/schemas/`, tested there from
  now on) and `src/agentdag/policy/tier-policy.yaml` (from `tier-policy.example.yaml`).
- Layer contract as in pyproject: domain imports nothing of ours; `application.kernel` and
  `application.workflows` import domain (and `application.graph_a_ports` for the two reused ports);
  adapters import application + domain; composition wires. `application.workflows` is under
  `application` on purpose: a workflow PROGRAM is coordinator code, and putting it above the layers
  would exempt it from the contract.
- Determinism (design 3.3), the cheap form for slice 1: primitives receive `Clock`; every `at`
  comes from `clock.now()`; the workflow module is AST-checked at load for `time.time`,
  `time.monotonic`, `datetime.now`, `datetime.utcnow`, `random.*`, `uuid.uuid4`, `os.urandom`,
  `secrets.*` and refused with `NondeterministicCallError` (Task 12). No stdlib monkeypatching.
- The run id is minted by the CLI (the scheduler, not coordinator code): `<utc stamp>-<6 hex from os.urandom>`
  (closes the M1 second-stamp collision); the run dir is `<runs>/<run_id>` at 0700, files 0600
  (design 3.1, D5: default `/var/lib/agentdag/runs`, overridable; the CLI refuses a missing or
  unwritable runs dir with `INVALID_ARGUMENT`, it never falls back to `/tmp`).
- Approve in M2 is the MINIMAL form: payload written, `state=suspended`, process exits; a decision
  is a file `decisions/<node_id>.json` written temp+rename by `agentdag run approve`; on relaunch
  the coordinator folds it into the journal as `approve_decision` with `by` = the local login name
  and `token_id: local`. Identity from a token, duplicate detection with an idempotency key, the
  deadline timer and the external-effect-default validation are M3 (mid plan). The M2 payload's
  `default` MUST still name an option with `effect: none` (design 2.4) - graph A's is `hold`.
- Credentials for a Claude node: `ClaudeExecutor` reads, AT THE CALL SITE, either an OAuth token
  keyfile (`credentials.claude_oauth_token_file`, default `~/.config/agentdag/claude-oauth-token`,
  produced by the user with `claude setup-token`) and passes it as `CLAUDE_CODE_OAUTH_TOKEN` in the
  child env with an EMPTY per-node `CLAUDE_CONFIG_DIR`, or, when no keyfile is configured, the M1
  per-node credential COPY (measured to work). Task 8 measures which one authenticates; Task 14
  builds both behind one seam.
- New runtime dependency: `pyyaml>=6.0.3` (the policy table). New dev dependency: `jsonschema>=4.26.0`
  (schema-conformance tests). Both floors are whatever `bmk deps` resolves at the time; write the
  version bmk pins.
- Tests carry the repo's markers: `os_agnostic` by default; `os_linux` + `local_only` for the
  systemd scope; `integration` for anything that calls a model. No test calls a model.
- Every task ends `make test` green (`env -u VIRTUAL_ENV BMK_PYTHON_CMD=$PWD/.venv/bin/python make test > /tmp/agentdag-m2-<task>.log 2>&1; echo RC=$? >> /tmp/agentdag-m2-<task>.log`,
  read `RC=` from the log), then a pathspec commit with a `-F` message file. `make test` stages the
  working tree (M1 note), so `git status --porcelain` is read after every gate run.

---

### Task 8: D3 - the subscription token, its terms and the headless path (RESEARCH; a document plus one probe)

**Files:**
- Create: `workflow/design/probes/d3-subscription-terms.md`
- Create: `workflow/probes/probe_oauth_token_env.py`
- Modify: `DECISIONS.md` (a "Decided" line for D3)

**Interfaces:**
- Consumes: the store fact `reference-claude-code-subscription-oauth-works-headlessly-the-refusal-is-per-runner`
  (read its body); `claude-agent-sdk` 0.2.139 in the agentdag `.venv`; the operator's own login.
- Produces: the D3 answer (`permitted | not permitted | unclear`, each with the quoted source and
  its date), and the MEASURED answer to "does `CLAUDE_CODE_OAUTH_TOKEN` in the child env
  authenticate an SDK child whose `CLAUDE_CONFIG_DIR` is an EMPTY directory?" - which Task 14's
  credential seam depends on.

**Out of scope** - do NOT touch, though they look related:
- The tier policy's `billing` field and the metered row: they stay whatever D3 says (the fallback
  is kept either way, high plan risk 3).
- Any change to `agentdag` code.

**STOP conditions** - stop and report rather than improvise, if:
- no OAuth token is available for the probe (`claude setup-token` is interactive and needs the
  user): write the terms half of the document, mark the probe UNMEASURED with the exact command the
  user must run, and stop;
- the terms text is behind a login or a captcha: quote what is public, tag the rest RELAYED.

- [ ] **Step 1: Read the terms and the docs first-hand.** WebFetch, in this order, and quote the
  sentences that bear on AUTOMATED / UNATTENDED use of a Claude subscription through Claude Code:
  `https://docs.anthropic.com/en/docs/claude-code/authentication` (or the current path for
  `setup-token`), `https://www.anthropic.com/legal/consumer-terms`,
  `https://www.anthropic.com/legal/aup` (usage policy), and the Claude Code docs page on
  headless / CI use (`claude -p`, `CLAUDE_CODE_OAUTH_TOKEN`). For each: URL, fetch date, the
  quoted sentence(s), and one line on what it means for an always-on coordinator dispatching
  nodes on the operator's Max/Pro plan. Tag every claim READ (docs) or RELAYED.

- [ ] **Step 2: The probe.** `workflow/probes/probe_oauth_token_env.py` (PEP 723, `claude-agent-sdk>=0.2.139`),
  run with the agentdag venv's python: three arms, each one `ClaudeSDKClient` turn "reply with the
  single word ok" with `setting_sources=[]`, `max_turns=1`, `permission_mode="dontAsk"`,
  `allowed_tools=[]`, `env` overriding `HOME` to a fresh temp dir and `CLAUDE_CONFIG_DIR` to a
  fresh EMPTY temp dir - arm A adds `CLAUDE_CODE_OAUTH_TOKEN` read from the keyfile
  `~/.config/agentdag/claude-oauth-token` (create it with `claude setup-token` beforehand, 0600;
  if absent, STOP as above); arm B adds nothing (the negative control, expected "Not logged in");
  arm C copies the operator's `~/.claude/.credentials.json` into the config dir (the M1 path,
  the positive control). Print per arm: `is_error`, the first 80 chars of the result text, and
  `usage`. Save output to `workflow/probes/probe_oauth_token_env.result.txt` with the secret NEVER
  printed (print only `token file: present, N bytes`).

- [ ] **Step 3: Write `workflow/design/probes/d3-subscription-terms.md`**: date, the quotes with
  URLs, the D3 answer, the three probe arms' results, and the consequence for Task 14 (which
  credential path is the default). Then add to `DECISIONS.md` "Decided": `D3 (2026-08-17): <answer>;
  the tier table keeps a metered row and a token budget regardless; the executor's default
  credential path is <keyfile|copy>, measured in probes/d3-subscription-terms.md`.

- [ ] **Step 4: Commit** in RESEARCH by pathspec (`git commit workflow/design/probes/d3-subscription-terms.md workflow/probes/probe_oauth_token_env.py workflow/probes/probe_oauth_token_env.result.txt DECISIONS.md -F <msg>`).

---

### Task 9: domain - the kernel records, the journal lines and the content-addressed key

**Files:**
- Create: `src/agentdag/domain/models.py`, `src/agentdag/domain/journal.py`, `src/agentdag/domain/keys.py`
- Modify: `src/agentdag/domain/errors.py` (append the kernel errors), `src/agentdag/domain/__init__.py` (re-export nothing new; keep it as it is if it re-exports nothing)
- Create: `src/agentdag/schemas/{node-spec,result-record,journal-line,approve-payload,handover,map-manifest}.schema.json` - byte copies of `workflow/design/schemas/*.schema.json`
- Modify: `pyproject.toml` (dev dep `jsonschema`)
- Test: `tests/test_kernel_domain.py`, `tests/test_kernel_schemas.py`

**Interfaces:**
- Consumes: nothing of ours.
- Produces (every later task imports these names exactly):
  - `models.py`: `Kind`, `NodeStatus`, `ErrorType`, `Isolation`, `TierRole`, `Executor` enums (`StrEnum`);
    `Budget(tokens: dict[str, int], usd: float | None = None)`; `Requirement(resource: str, amount: float)`;
    `NodeSpec` (frozen; fields of design 2.1 plus `continuation: int = 0`); `Tokens(in_: int | None, out: int | None, cache_read: int | None, reasoning: int | None)`
    (alias `in`); `NodeError(type: ErrorType, message: str, transient: bool)`; `KnowledgeUsed(dataset: str, content_hash: str)`;
    `NodeOutcome` (what a node BODY returns: `status`, `artefact_refs`, `key_facts`, `typed_fields`, `tokens`, `charged_tokens`, `model_used`, `executor_used`, `effort_used`, `knowledge_used`, `error`);
    `ResultRecord` (design 2.2, `= NodeOutcome + node_id, attempt, input_hash, duration_s, cost_usd`);
    `RunStatus` enum (`running | suspended | done | failed | crashed | cancelling | cancelled`); `LockHolder(host, boot_id, pid, pid_start_time)`;
    `RunState(run_id, workflow, args, owner, status, cursor, policy_version, tokens_by_row, holder)`; `ApproveOption(id, label, effect)`; `ApprovePayload` (schema fields); `Decision(node_id, decision, reason, by, token_id)`.
  - `journal.py`: `StartedLine`, `ResultLine`, `RunStartedLine`, `ResumeLine`, `ApproveDecisionLine`, `RunSummaryLine`, the union `JournalLine` (discriminated on `event`), `parse_journal_line(text: str) -> JournalLine`, `dump_journal_line(line: JournalLine) -> str` (one line, no newline, sorted keys).
  - `keys.py`: `canonical_json(value) -> str`, `content_hash(text: str) -> str` (`sha256:<hex>`), `record_hash(record: ResultRecord) -> str`, `prefix_hash(dep_records: Sequence[ResultRecord]) -> str`, `journal_key(spec: NodeSpec, *, brief_hash: str, input_hash: str, prefix: str) -> str` (`v2:sha256:<hex>`), `hash8(key: str) -> str`.
  - `errors.py`: `KernelError(Exception)`, `LockHeld(KernelError)`, `NondeterministicCallError(KernelError)`, `WorkflowNotFound(KernelError)`, `SpecRejected(KernelError)`, `Suspended(Exception)` (control flow: `node_id`), `RunRefused(KernelError)`.

**Out of scope** - do NOT touch, though they look related:
- `domain/graph_a.py`, `domain/enums.py` (the template's CLI enums; the kernel enums live in `models.py` beside the models they type).
- The design copies in `workflow/design/schemas/` (RESEARCH): the code repo now owns tested copies; a drift check between the two is a later chore, not this task.

**STOP conditions** - stop and report rather than improvise, if:
- a schema example in `src/agentdag/schemas/*.json` does NOT validate against its own schema with `jsonschema` (then the design copy is broken; report which);
- pyright strict rejects the `in` alias on `Tokens` (use `Field(alias="in")` with `populate_by_name=True`; if that still fails, name the field `in_` and serialise via `serialization_alias="in"` - report which form landed).

- [ ] **Step 1: RED - `tests/test_kernel_domain.py`**

```python
from __future__ import annotations
import json
import pytest
from agentdag.domain.errors import Suspended
from agentdag.domain.journal import ResultLine, StartedLine, dump_journal_line, parse_journal_line
from agentdag.domain.keys import canonical_json, content_hash, hash8, journal_key, prefix_hash, record_hash
from agentdag.domain.models import (
    Budget,
    ErrorType,
    Isolation,
    Kind,
    NodeError,
    NodeOutcome,
    NodeSpec,
    NodeStatus,
    ResultRecord,
    TierRole,
    Tokens,
)


def spec(**over: object) -> NodeSpec:
    base = dict(
        node_id="w_migrate@1",
        kind=Kind.WORK,
        brief_ref="nodes/w_migrate@1/00000000/brief.md",
        executor="claude",
        tier_role=TierRole.STANDARD,
        write_set=["wt/r/**"],
        requires=[],
        isolation=Isolation.WORKTREE,
        deps=["g_discover"],
        deadline_s=3600,
        budget=Budget(tokens={"sonnet": 400_000}),
        attempt=0,
    )
    base.update(over)
    return NodeSpec.model_validate(base)


def record(node_id: str = "g_discover", status: NodeStatus = NodeStatus.DONE) -> ResultRecord:
    return ResultRecord(
        node_id=node_id,
        attempt=0,
        status=status,
        artefact_refs=[],
        key_facts={"n": 2},
        typed_fields=["n"],
        tokens=None,
        charged_tokens={},
        cost_usd=None,
        duration_s=0.1,
        executor_used="code",
        model_used="-",
        effort_used="-",
        knowledge_used=[],
        input_hash="sha256:0",
    )


def test_canonical_json_is_sorted_compact_and_stable() -> None:
    assert canonical_json({"b": 1, "a": [2, {"d": None, "c": "x"}]}) == '{"a":[2,{"c":"x","d":null}],"b":1}'


def test_journal_key_ignores_limits_and_display_but_not_inputs() -> None:
    k = journal_key(
        spec(), brief_hash=content_hash("brief"), input_hash=content_hash("input"), prefix=prefix_hash([record()])
    )
    assert k.startswith("v2:sha256:") and len(hash8(k)) == 8
    same = journal_key(
        spec(deadline_s=1, budget=Budget(tokens={"sonnet": 1})),
        brief_hash=content_hash("brief"),
        input_hash=content_hash("input"),
        prefix=prefix_hash([record()]),
    )
    assert same == k  # deadline and budget are limits, not identity
    for changed in (
        spec(attempt=1),
        spec(continuation=1),
        spec(model="opus"),
        spec(write_set=["wt/other/**"]),
        spec(isolation=Isolation.DIR),
    ):
        assert (
            journal_key(
                changed,
                brief_hash=content_hash("brief"),
                input_hash=content_hash("input"),
                prefix=prefix_hash([record()]),
            )
            != k
        )
    assert (
        journal_key(
            spec(), brief_hash=content_hash("brief2"), input_hash=content_hash("input"), prefix=prefix_hash([record()])
        )
        != k
    )
    assert (
        journal_key(
            spec(),
            brief_hash=content_hash("brief"),
            input_hash=content_hash("input"),
            prefix=prefix_hash([record(status=NodeStatus.FAILED)]),
        )
        != k
    )


def test_record_hash_is_content_addressed() -> None:
    assert record_hash(record()) == record_hash(record()) and record_hash(record()) != record_hash(record(node_id="x"))


def test_journal_line_round_trips_and_is_one_line() -> None:
    line = StartedLine(key="v2:sha256:" + "0" * 64, node_id="n", attempt=0, at="2026-08-17T09:12:03+00:00")
    text = dump_journal_line(line)
    assert "\n" not in text and parse_journal_line(text) == line
    result = ResultLine(key=line.key, record=record(), at="2026-08-17T09:12:41+00:00")
    assert json.loads(dump_journal_line(result))["record"]["tokens"] is None
    with pytest.raises(ValueError):
        parse_journal_line('{"event": "nope", "at": "2026-08-17T09:12:03+00:00"}')


def test_timestamps_must_be_utc_with_explicit_offset() -> None:
    with pytest.raises(ValueError):
        StartedLine(key="v2:sha256:" + "0" * 64, node_id="n", attempt=0, at="2026-08-17T09:12:03Z")


def test_tokens_serialise_the_schema_field_name_in() -> None:
    assert Tokens(**{"in": 1, "out": 2, "cache_read": 3, "reasoning": 0}).model_dump(by_alias=True) == {
        "in": 1,
        "out": 2,
        "cache_read": 3,
        "reasoning": 0,
    }


def test_node_error_carries_the_closed_vocabulary() -> None:
    assert NodeError(type=ErrorType.AGENTS_EMPTY_RESULT, message="", transient=False).type == "agents_empty_result"
    with pytest.raises(ValueError):
        NodeError.model_validate({"type": "oops", "message": "", "transient": False})


def test_node_outcome_defaults_are_the_empty_shapes() -> None:
    o = NodeOutcome(status=NodeStatus.DONE, executor_used="code", model_used="-", effort_used="-")
    assert (o.artefact_refs, o.key_facts, o.typed_fields, o.charged_tokens, o.tokens, o.error) == (
        [],
        {},
        [],
        {},
        None,
        None,
    )


def test_suspended_names_the_node() -> None:
    assert Suspended("a_push_list").node_id == "a_push_list"
```
Run: `.venv/bin/python -m pytest tests/test_kernel_domain.py -q` -> ImportError.

- [ ] **Step 2: GREEN - `src/agentdag/domain/models.py`**

```python
"""Kernel records: what a node is (spec), what it returned (record), what a run is (state).

Mirrors the JSON schemas shipped in ``agentdag/schemas/`` (design 2.1, 2.2, 3.1, 3.4);
``tests/test_kernel_schemas.py`` proves the two agree. Frozen where a value enters a
journal key. No I/O here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ApproveOption",
    "ApprovePayload",
    "Budget",
    "Decision",
    "ErrorType",
    "Executor",
    "Isolation",
    "Kind",
    "KnowledgeUsed",
    "LockHolder",
    "NodeError",
    "NodeOutcome",
    "NodeSpec",
    "NodeStatus",
    "Requirement",
    "ResultRecord",
    "RunState",
    "RunStatus",
    "TierRole",
    "Tokens",
]


class Kind(StrEnum):
    WORK = "work"
    GATE = "gate"
    SYNTH = "synth"
    PLANNER = "planner"
    APPROVE = "approve"
    MAP = "map"
    REDUCE = "reduce"
    WAIT = "wait"
    BATCH = "batch"
    STAGE = "stage"
    APPLY = "apply"


class NodeStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_CONTEXT = "needs_context"
    NEEDS_CONTINUATION = "needs_continuation"
    REFUSED = "refused"
    CANCELLED = "cancelled"


class ErrorType(StrEnum):
    AGENTS_EMPTY_RESULT = "agents_empty_result"
    AUTH_FAILURE = "auth_failure"
    DEADLINE = "deadline"
    EXECUTOR_ERROR = "executor_error"
    SCHEMA_MISMATCH = "schema_mismatch"
    KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED_BY_USER = "cancelled_by_user"


class Isolation(StrEnum):
    WORKTREE = "worktree"
    DIR = "dir"
    NONE = "none"


class TierRole(StrEnum):
    MECHANICAL = "mechanical"
    STANDARD = "standard"
    DEEP = "deep"
    TOP = "top"


class Executor(StrEnum):
    """The two executor families slice 1 knows by name; ``mcp:<server>/<tool>`` strings stay free text on the spec."""

    CLAUDE = "claude"
    CODE = "code"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUSPENDED = "suspended"
    DONE = "done"
    FAILED = "failed"
    CRASHED = "crashed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class Budget(BaseModel):
    model_config = ConfigDict(frozen=True)
    tokens: dict[str, int] = Field(default_factory=dict)
    usd: float | None = None


class Requirement(BaseModel):
    model_config = ConfigDict(frozen=True)
    resource: str
    amount: float


class NodeSpec(BaseModel):
    """Design 2.1. ``continuation`` is the 3.8 successor counter; ``compact`` is left for the long-node milestone."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    node_id: str = Field(min_length=1)
    kind: Kind
    brief_ref: str = ""
    executor: str | None = None
    tier_role: TierRole | None = None
    model: str | None = None
    effort: str | None = None
    knowledge: list[str] = Field(default_factory=list)
    stage_into: str | None = None
    write_set: list[str] = Field(default_factory=list)
    requires: list[Requirement] = Field(default_factory=list)
    isolation: Isolation = Isolation.NONE
    deps: list[str] = Field(default_factory=list)
    deadline_s: float
    budget: Budget = Field(default_factory=Budget)
    attempt: int = 0
    continuation: int = 0


class Tokens(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    in_: int | None = Field(alias="in")
    out: int | None
    cache_read: int | None
    reasoning: int | None


class NodeError(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: ErrorType
    message: str
    transient: bool


class KnowledgeUsed(BaseModel):
    model_config = ConfigDict(frozen=True)
    dataset: str
    content_hash: str


class NodeOutcome(BaseModel):
    """What a node BODY hands back; the dispatcher completes it into a :class:`ResultRecord`."""

    model_config = ConfigDict(frozen=True)
    status: NodeStatus
    artefact_refs: list[str] = Field(default_factory=list)
    key_facts: dict[str, Any] = Field(default_factory=dict)
    typed_fields: list[str] = Field(default_factory=list)
    tokens: Tokens | None = None
    charged_tokens: dict[str, int] = Field(default_factory=dict)
    executor_used: str
    model_used: str
    effort_used: str
    knowledge_used: list[KnowledgeUsed] = Field(default_factory=list)
    error: NodeError | None = None


class ResultRecord(NodeOutcome):
    """Design 2.2: the ONLY thing the coordinator branches on."""

    node_id: str
    attempt: int
    input_hash: str
    duration_s: float
    cost_usd: float | None = None


class LockHolder(BaseModel):
    model_config = ConfigDict(frozen=True)
    host: str
    boot_id: str
    pid: int
    pid_start_time: str


class RunState(BaseModel):
    run_id: str
    workflow: str
    args: dict[str, Any]
    owner: str
    status: RunStatus
    cursor: str | None = None
    policy_version: str
    tokens_by_row: dict[str, int] = Field(default_factory=dict)
    holder: LockHolder | None = None


class ApproveOption(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    label: str
    effect: str = Field(pattern="^(none|external)$")


class ApprovePayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    node_id: str
    artefact_refs: list[str]
    options: list[ApproveOption]
    default: str
    decide_by: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
    workflow: str
    run_id: str


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)
    node_id: str
    decision: str
    reason: str = ""
    by: str
    token_id: str
```
(Write the enum members and short models one per line in the real file; the compressed form above is for the plan's width. `Field(min_length=1)` on ids where the schema says so.)

`src/agentdag/domain/journal.py`:
```python
"""Journal lines (design 3.1/3.2): the six events slice 1 emits, one JSON object per line."""

from __future__ import annotations
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from .models import ResultRecord

__all__ = [
    "ApproveDecisionLine",
    "JournalLine",
    "ResultLine",
    "ResumeLine",
    "RunStartedLine",
    "RunSummaryLine",
    "StartedLine",
    "dump_journal_line",
    "parse_journal_line",
]

_AT = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


class _Line(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    at: str = _AT


class StartedLine(_Line):
    event: Literal["started"] = "started"
    key: str
    node_id: str
    attempt: int


class ResultLine(_Line):
    event: Literal["result"] = "result"
    key: str
    record: ResultRecord


class RunStartedLine(_Line):
    event: Literal["run_started"] = "run_started"
    run_id: str
    workflow: str
    args: dict[str, Any]
    by: str
    token_id: str
    policy_version: str


class ResumeLine(_Line):
    event: Literal["resume"] = "resume"
    run_id: str
    reason: Literal["decision", "crash", "restart", "manual"]
    by: str
    token_id: str


class ApproveDecisionLine(_Line):
    event: Literal["approve_decision"] = "approve_decision"
    node_id: str
    decision: str
    reason: str
    by: str
    token_id: str


class RunSummaryLine(_Line):
    event: Literal["run_summary"] = "run_summary"
    run_id: str
    policy_version: str
    overhead_fraction: dict[str, float]
    citation_coverage: list[dict[str, Any]]
    journal_bytes: int
    replay_seconds: float | None
    records_per_node: float
    tokens_by_row: dict[str, int]
    journal_lines: int
    human_interactions: int


JournalLine = Annotated[
    Union[StartedLine, ResultLine, RunStartedLine, ResumeLine, ApproveDecisionLine, RunSummaryLine],
    Field(discriminator="event"),
]
_ADAPTER: TypeAdapter[JournalLine] = TypeAdapter(JournalLine)


def parse_journal_line(text: str) -> JournalLine:
    """One JSON object -> the typed line; raises ``ValueError`` (pydantic's) on any other shape."""
    return _ADAPTER.validate_json(text)


def dump_journal_line(line: JournalLine) -> str:
    """The line as ONE line of compact JSON with sorted keys, no trailing newline; ``in`` keeps its schema name."""
    return _ADAPTER.dump_json(
        line, by_alias=True, exclude_none=False
    ).decode()  # then json.loads/json.dumps(sort_keys=True, separators=(",", ":")) if dump_json does not sort
```
(pydantic's `dump_json` does not sort keys; implement `dump_journal_line` as `json.dumps(json.loads(...), sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.)

`src/agentdag/domain/keys.py`:
```python
"""The content-addressed journal key (design 3.2): which fields IDENTIFY a call, stated in code."""

from __future__ import annotations
import hashlib, json
from collections.abc import Sequence
from typing import Any
from .models import NodeSpec, ResultRecord

__all__ = ["KEY_VERSION", "canonical_json", "content_hash", "hash8", "journal_key", "prefix_hash", "record_hash"]
KEY_VERSION = "v2"
_IDENTITY_FIELDS = (
    "kind",
    "executor",
    "tier_role",
    "model",
    "effort",
    "knowledge",
    "stage_into",
    "write_set",
    "requires",
    "isolation",
    "deps",
    "attempt",
    "continuation",
)  # deadline_s, budget, brief_ref (a path) are NOT identity


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_hash(record: ResultRecord) -> str:
    return content_hash(canonical_json(record.model_dump(mode="json", by_alias=True)))


def prefix_hash(dep_records: Sequence[ResultRecord]) -> str:
    return content_hash("\0".join(record_hash(r) for r in dep_records))


def journal_key(spec: NodeSpec, *, brief_hash: str, input_hash: str, prefix: str) -> str:
    identity = spec.model_dump(mode="json", include=set(_IDENTITY_FIELDS))
    identity["brief_hash"] = brief_hash
    identity["input_hash"] = input_hash
    digest = hashlib.sha256((prefix + "\0" + canonical_json(identity)).encode("utf-8")).hexdigest()
    return f"{KEY_VERSION}:sha256:{digest}"


def hash8(key: str) -> str:
    return key.rsplit(":", 1)[-1][:8]
```

`src/agentdag/domain/errors.py` - append:
```python
class KernelError(Exception):
    """Base of the coordinator's typed errors."""


class LockHeld(KernelError):
    """Another live coordinator holds this run dir's lock."""


class NondeterministicCallError(KernelError):
    """A workflow module reaches for the clock or randomness; that breaks resume (design 3.3)."""


class WorkflowNotFound(KernelError):
    """No built-in workflow of that name."""


class SpecRejected(KernelError):
    """Whole-spec validation refused a node (design 2.4)."""


class RunRefused(KernelError):
    """run.start / resume refused before anything ran (missing runs dir, live lock, bad args)."""


class Suspended(Exception):
    """Control flow, not an error: an approve node has no decision yet, the coordinator exits (design 3.4)."""

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id)
        self.node_id = node_id
```
and add the seven names to `__all__`.

Run the domain tests -> green. Commit (`git add src/agentdag/domain/models.py src/agentdag/domain/journal.py src/agentdag/domain/keys.py src/agentdag/domain/errors.py tests/test_kernel_domain.py`; `git commit <those paths> -F <msg>`).

- [ ] **Step 3: RED - `tests/test_kernel_schemas.py`** (schema conformance, the seam between design and code)

Copy the six schema files first: `cp <workspace>/projects/public/KI/RESEARCH/workflow/design/schemas/*.schema.json src/agentdag/schemas/` and add `jsonschema>=<resolved>` to `[project.optional-dependencies] dev` in `pyproject.toml` (edit with the toml tooling the `bitranox:files-edit-toml` skill names, never sed).
```python
from __future__ import annotations
import json
from importlib.resources import files
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from agentdag.domain.journal import parse_journal_line, dump_journal_line
from agentdag.domain.models import ApprovePayload, NodeSpec, ResultRecord


def load(name: str) -> dict:
    return json.loads((files("agentdag.schemas") / f"{name}.schema.json").read_text())


def validator(name: str) -> Draft202012Validator:
    reg = Registry()
    for other in ("node-spec", "result-record", "journal-line", "approve-payload", "handover", "map-manifest"):
        s = load(other)
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
        reg = reg.with_resource(f"{other}.schema.json", Resource.from_contents(s))
    return Draft202012Validator(load(name), registry=reg)


@pytest.mark.parametrize("name", ["node-spec", "result-record", "journal-line", "approve-payload"])
def test_every_schema_example_validates_against_its_schema(name: str) -> None:
    v = validator(name)
    for example in load(name)["examples"]:
        v.validate(example)


def test_models_accept_the_schema_examples_and_emit_valid_json() -> None:
    for ex in load("node-spec")["examples"]:
        NodeSpec.model_validate({k: v for k, v in ex.items() if k != "compact"})  # compact is not a slice-1 field
    for ex in load("result-record")["examples"]:
        validator("result-record").validate(json.loads(ResultRecord.model_validate(ex).model_dump_json(by_alias=True)))
    for ex in load("approve-payload")["examples"]:
        validator("approve-payload").validate(json.loads(ApprovePayload.model_validate(ex).model_dump_json()))
    for ex in load("journal-line")["examples"]:
        if ex["event"] in {"started", "result", "run_started", "resume", "approve_decision", "run_summary"}:
            validator("journal-line").validate(json.loads(dump_journal_line(parse_journal_line(json.dumps(ex)))))
```
Run -> fails on the missing package data / dev dep; then GREEN: the copies exist, the dep is installed (`uv pip install -e ".[dev]" --python .venv/bin/python`), and every model change the test demands is made in `models.py` (the STOP condition covers a broken example). If `NodeSpec` examples carry `compact`, add `compact: dict[str, int] | None = None` to `NodeSpec` (it is in the schema; keeping it costs nothing and it is in the key by design 3.2). Run -> green.

- [ ] **Step 4: Gate and commit.** `make test` -> RC=0 (pyright strict will insist on `referencing` types; it ships them). Commit by pathspec: the two test files, `src/agentdag/schemas/`, `pyproject.toml`, `uv.lock` if bmk touched it.

---

### Task 10: ports, the JSONL journal with replay, the run lock, the clock

**Files:**
- Create: `src/agentdag/application/kernel/__init__.py` (empty docstring module), `src/agentdag/application/kernel/ports.py`, `src/agentdag/application/kernel/replay.py`
- Create: `src/agentdag/adapters/kernel/__init__.py`, `src/agentdag/adapters/kernel/journal_jsonl.py`, `src/agentdag/adapters/kernel/lock_file.py`, `src/agentdag/adapters/kernel/clock_utc.py`
- Test: `tests/test_kernel_journal.py`, `tests/test_kernel_lock.py`

**Interfaces:**
- Consumes: Task 9's `JournalLine`, `parse_journal_line`, `dump_journal_line`, `ResultRecord`, `LockHolder`, `LockHeld`, `NodeSpec`, `NodeOutcome`.
- Produces (`ports.py`):
  ```python
  class Clock(Protocol):
      def now(self) -> datetime: ...                       # tz-aware UTC
  class Journal(Protocol):
      def append(self, line: JournalLine) -> None: ...     # ONE writer, O_APPEND, also copied to audit
      def lines(self) -> list[JournalLine]: ...
  class RunLock(Protocol):
      def acquire(self, run_dir: Path, holder: LockHolder) -> LockToken: ...   # raises LockHeld
      def release(self, token: LockToken) -> None: ...
  @dataclass(frozen=True) class LockToken: path: Path; holder: LockHolder
  @dataclass(frozen=True) class ExecutorRequest: node_dir: Path; cwd: Path; brief: str; prompt: str; model: str; effort: str | None; max_turns: int; isolation_root: Path; write_set: tuple[str, ...]; deny_bash: tuple[str, ...]
  class Executor(Protocol):
      async def run(self, request: ExecutorRequest) -> NodeOutcome: ...
  class Scope(Protocol):
      def start(self, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> ScopeHandle: ...
      def is_alive(self, handle: ScopeHandle) -> bool: ...
      def kill(self, handle: ScopeHandle) -> bool: ...     # True only when the cgroup (or process) is verified gone
  @dataclass(frozen=True) class ScopeHandle: unit: str; pid: int
  ```
  and `replay.py`: `ReplayIndex(results: dict[str, ResultRecord], crash_window: set[str], decisions: dict[str, ApproveDecisionLine], key_sequence: list[str], run_started: RunStartedLine | None)`, `build_replay_index(lines: Sequence[JournalLine]) -> ReplayIndex` (pure).
  Adapters: `JsonlJournal(journal_path: Path, audit_path: Path)`, `FileRunLock()`, `UtcClock()`, plus `current_holder() -> LockHolder` in `lock_file.py` (host, `/proc/sys/kernel/random/boot_id` or `"-"` off Linux, pid, the process start time from `/proc/<pid>/stat` field 22 or `psutil`-free fallback `"-"`) and `holder_is_alive(holder) -> bool` (pid exists AND its start time matches; a bare pid is never enough - design 3.4).

**Out of scope** - do NOT touch, though they look related:
- `application/ports.py` and `application/graph_a_ports.py` (the template's and the baseline's ports).
- The gate's `FileLock` (Task 13) and the bmk-tool-env host lease (M3).

**STOP conditions** - stop and report rather than improvise, if:
- `/proc/<pid>/stat` parsing is not available on the CI OS you are on: the fallback `"-"` start time makes `holder_is_alive` degrade to pid-only; if that happens on LINUX, stop - it must not;
- O_APPEND single-writer semantics cannot be asserted on Windows CI (the test below writes from ONE process; if the append test itself fails on windows-latest, report the traceback, do not skip it).

- [ ] **Step 1: RED - `tests/test_kernel_journal.py`**

```python
from __future__ import annotations
from pathlib import Path
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.application.kernel.replay import build_replay_index
from agentdag.domain.journal import ApproveDecisionLine, ResultLine, RunStartedLine, StartedLine
from agentdag.domain.models import NodeStatus, ResultRecord

AT = "2026-08-17T09:12:03+00:00"


def rec(node_id: str) -> ResultRecord:
    return ResultRecord(
        node_id=node_id,
        attempt=0,
        status=NodeStatus.DONE,
        executor_used="code",
        model_used="-",
        effort_used="-",
        input_hash="sha256:0",
        duration_s=0.0,
    )


def key(n: int) -> str:
    return "v2:sha256:" + f"{n:064x}"


def test_journal_appends_one_line_per_call_and_replays_them_typed(tmp_path: Path) -> None:
    j = JsonlJournal(tmp_path / "journal.jsonl", tmp_path / "audit.jsonl")
    j.append(
        RunStartedLine(run_id="r", workflow="w", args={}, by="me", token_id="local", policy_version="sha256:p", at=AT)
    )
    j.append(StartedLine(key=key(1), node_id="a", attempt=0, at=AT))
    j.append(ResultLine(key=key(1), record=rec("a"), at=AT))
    j.append(StartedLine(key=key(2), node_id="b", attempt=0, at=AT))
    assert (tmp_path / "journal.jsonl").read_text().count("\n") == 4 and (tmp_path / "audit.jsonl").read_text() == (
        tmp_path / "journal.jsonl"
    ).read_text()
    idx = build_replay_index(j.lines())
    assert set(idx.results) == {key(1)} and idx.crash_window == {key(2)} and idx.key_sequence == [key(1), key(2)]
    assert idx.run_started is not None and idx.run_started.run_id == "r"


def test_replay_index_keeps_the_latest_decision_per_node_and_the_result_of_a_repeated_key(tmp_path: Path) -> None:
    lines = [
        ApproveDecisionLine(node_id="a_push_list", decision="hold", reason="", by="me", token_id="local", at=AT),
        StartedLine(key=key(3), node_id="c", attempt=0, at=AT),
        StartedLine(key=key(3), node_id="c", attempt=0, at=AT),
        ResultLine(key=key(3), record=rec("c"), at=AT),
    ]
    idx = build_replay_index(lines)
    assert idx.decisions["a_push_list"].decision == "hold" and key(3) in idx.results and idx.crash_window == set()
    assert idx.key_sequence == [
        key(3),
        key(3),
    ]  # every started is a dispatch attempt; the sequence is the replay-purity oracle


def test_journal_files_are_owner_only_and_a_torn_last_line_is_reported_not_swallowed(tmp_path: Path) -> None:
    import os, sys, pytest

    j = JsonlJournal(tmp_path / "journal.jsonl", tmp_path / "audit.jsonl")
    j.append(StartedLine(key=key(1), node_id="a", attempt=0, at=AT))
    if sys.platform != "win32":
        assert os.stat(tmp_path / "journal.jsonl").st_mode & 0o777 == 0o600
    with (tmp_path / "journal.jsonl").open("a") as f:
        f.write('{"event": "started", "key": "v2:s')
    with pytest.raises(ValueError, match="line 2"):
        j.lines()
```
Run -> ImportError.

- [ ] **Step 2: GREEN** - `ports.py` and `replay.py` as in Interfaces (replay: iterate lines; `started` appends the key to `key_sequence` and adds it to `crash_window`; `result` moves it to `results` and discards it from `crash_window`; `approve_decision` overwrites `decisions[node_id]`; `run_started` sets `run_started`), `journal_jsonl.py`:

```python
"""Journal on disk: ONE writer, O_APPEND, one JSON object per line, audit as a superset copy (design 3.1)."""

from __future__ import annotations
import os
from pathlib import Path
from ...domain.journal import JournalLine, dump_journal_line, parse_journal_line

__all__ = ["JsonlJournal"]
_OWNER_ONLY = 0o600


class JsonlJournal:
    def __init__(self, journal_path: Path, audit_path: Path) -> None:
        self._journal = journal_path
        self._audit = audit_path

    def append(self, line: JournalLine) -> None:
        text = dump_journal_line(line) + "\n"
        for path in (self._journal, self._audit):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _OWNER_ONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

    def lines(self) -> list[JournalLine]:
        if not self._journal.exists():
            return []
        out: list[JournalLine] = []
        for number, raw in enumerate(self._journal.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                out.append(parse_journal_line(raw))
            except ValueError as exc:
                raise ValueError(f"journal line {number} is unreadable: {exc}") from exc
        return out
```
`clock_utc.py`: `class UtcClock: def now(self) -> datetime: return datetime.now(timezone.utc)` - the ONE place the kernel calls the clock; and a helper `def stamp(clock: Clock) -> str: return clock.now().isoformat(timespec="seconds").replace("+00:00", "+00:00")` that ASSERTS `tzinfo == timezone.utc` and returns `YYYY-MM-DDTHH:MM:SS+00:00` (put `stamp` in `application/kernel/ports.py` beside `Clock`, it is pure). Run the journal tests -> green. Commit by pathspec.

- [ ] **Step 3: RED - `tests/test_kernel_lock.py`**

```python
from __future__ import annotations
import json, os
from pathlib import Path
import pytest
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder, holder_is_alive
from agentdag.domain.errors import LockHeld
from agentdag.domain.models import LockHolder


def test_lock_is_exclusive_and_records_the_holder(tmp_path: Path) -> None:
    lock = FileRunLock()
    me = current_holder()
    token = lock.acquire(tmp_path, me)
    assert json.loads((tmp_path / "lock").read_text())["pid"] == os.getpid()
    with pytest.raises(LockHeld):
        lock.acquire(tmp_path, me)  # a second coordinator on the same run dir is refused
    lock.release(token)
    lock.acquire(tmp_path, me)  # after release the dir is free again


def test_a_stale_lock_of_a_dead_holder_is_broken(tmp_path: Path) -> None:
    dead = LockHolder(host=current_holder().host, boot_id=current_holder().boot_id, pid=2**22 - 1, pid_start_time="1")
    (tmp_path / "lock").write_text(dead.model_dump_json())
    assert not holder_is_alive(dead)
    FileRunLock().acquire(tmp_path, current_holder())  # no LockHeld: the recorded process is proven gone


def test_a_live_pid_with_a_different_start_time_is_not_the_holder() -> None:
    me = current_holder()
    assert holder_is_alive(me)
    assert not holder_is_alive(
        me.model_copy(update={"pid_start_time": me.pid_start_time + "x"})
    )  # a reused pid is never the test on its own
```
Run -> ImportError. GREEN: `lock_file.py` - `current_holder()` reads `socket.gethostname()`, `/proc/sys/kernel/random/boot_id` (else `"-"`), `os.getpid()`, and the start time from `/proc/<pid>/stat` (the 22nd field after the last `)`, else `"-"`); `holder_is_alive(h)`: `os.kill(pid, 0)` succeeds (`PermissionError` counts as alive) AND (`h.pid_start_time == "-"` or it equals the current start time of that pid); on Windows `os.kill(pid, 0)` is not a probe - use `ctypes.windll.kernel32.OpenProcess` or `psutil`-free `os.waitpid` is not available either, so on `win32` use `subprocess.run(["tasklist", "/FI", f"PID eq {pid}"])`, contains the pid -> alive (document why). `FileRunLock.acquire`: `os.open(run_dir / "lock", O_WRONLY|O_CREAT|O_EXCL, 0o600)`; on `FileExistsError` read the holder, if `holder_is_alive` raise `LockHeld(f"run dir {run_dir} is held by pid {h.pid} on {h.host} since boot {h.boot_id}")`, else `unlink` and retry ONCE. `release`: unlink if the file's holder is ours. Run -> green.

- [ ] **Step 4: Gate and commit** by pathspec (`src/agentdag/application/kernel/{__init__,ports,replay}.py src/agentdag/adapters/kernel/{__init__,journal_jsonl,lock_file,clock_utc}.py tests/test_kernel_journal.py tests/test_kernel_lock.py`).

---

### Task 11: the run directory on disk (design 3.1) and the git stderr fix

**Files:**
- Create: `src/agentdag/adapters/kernel/run_store_fs.py`
- Modify: `src/agentdag/application/kernel/ports.py` (add the `RunDir` protocol), `src/agentdag/adapters/graph_a/git_cli.py:34-55` (`_git`: put `stderr` into the raised error - the M1 leftover)
- Test: `tests/test_kernel_run_dir.py`; extend `tests/test_graph_a_adapters.py` with one test for the stderr fix

**Interfaces:**
- Consumes: `GitPort` (`clone` from a bare mirror; the kernel takes worktrees from a bare mirror under the run dir, design 3.1) - reused unchanged except for `_git`'s error text.
- Produces (`ports.py`):
  ```python
  class RunDir(Protocol):
      root: Path
      journal_path: Path
      audit_path: Path
      state_path: Path
      decisions_dir: Path

      def node_dir(self, node_id: str, hash8: str) -> Path: ...  # nodes/<node_id>/<hash8>/, created 0700
      def worktree(self, name: str) -> Path: ...  # wt/<name>
      def intents_dir(self, kind: str) -> Path: ...  # intents/<kind>/, created
      def marker(self, kind: str, key: str) -> Path: ...  # done/<kind>/<key>
      def artefacts_dir(self) -> Path: ...  # artefacts/
      def manifest_path(self, map_id: str) -> Path: ...  # manifest/<map_id>.json
      def write_atomic(self, rel: str, text: str) -> Path: ...  # temp+rename, 0600
      def read_state(self) -> RunState: ...
      def write_state(self, state: RunState) -> None: ...  # atomic
      def read_decision(self, node_id: str) -> Decision | None: ...  # decisions/<node_id>.json
      def write_decision(self, decision: Decision) -> None: ...  # temp+rename; refuses to overwrite an existing one
  ```
  Adapter: `FsRunDir(root)`, `FsRunDir.create(runs_base: Path, run_id: str) -> FsRunDir` (`mkdir(0o700)`, refuses an existing dir), `FsRunDir.open(runs_base, run_id)` (must exist).

**Out of scope** - do NOT touch, though they look related:
- `adapters/graph_a/store_fs.py` (the baseline's flat layout stays; the kernel has its own).
- Retention, the disk-ceiling probe, worktree pruning at startup (design 3.1 "Retention" - M3/M5).

**STOP conditions** - stop and report rather than improvise, if:
- Windows CI cannot express 0700/0600 (it cannot): the mode assertions are `sys.platform != "win32"` only; if a NON-mode assertion fails on Windows, report it;
- `write_decision` on an existing file must REFUSE (`FileExistsError`) - if you find yourself overwriting, stop: duplicate detection in M3 builds on it.

- [ ] **Step 1: RED - `tests/test_kernel_run_dir.py`**

```python
from __future__ import annotations
import json, os, sys
from pathlib import Path
import pytest
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.domain.models import Decision, RunState, RunStatus


def state() -> RunState:
    return RunState(
        run_id="r1", workflow="graph-a", args={}, owner="me", status=RunStatus.RUNNING, policy_version="sha256:p"
    )


def test_create_lays_out_the_run_dir_owner_only_and_refuses_to_reuse_it(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    for rel in ("decisions", "intents", "artefacts", "wt", "nodes", "manifest", "done"):
        assert (rd.root / rel).is_dir()
    if sys.platform != "win32":
        assert os.stat(rd.root).st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        FsRunDir.create(tmp_path, "r1")
    assert FsRunDir.open(tmp_path, "r1").root == rd.root


def test_node_dir_is_keyed_by_node_id_and_hash8_and_writes_are_atomic_and_owner_only(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    nd = rd.node_dir("w_migrate@1", "71efdc61")
    assert nd == rd.root / "nodes" / "w_migrate@1" / "71efdc61" and nd.is_dir()
    p = rd.write_atomic("artefacts/x.json", "{}")
    assert p.read_text() == "{}" and not list(rd.root.glob("artefacts/*.tmp*"))
    if sys.platform != "win32":
        assert os.stat(p).st_mode & 0o777 == 0o600


def test_state_round_trips_and_decisions_are_write_once(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    rd.write_state(state())
    assert rd.read_state() == state()
    assert rd.read_decision("a_push_list") is None
    d = Decision(node_id="a_push_list", decision="hold", by="me", token_id="local")
    rd.write_decision(d)
    assert rd.read_decision("a_push_list") == d
    with pytest.raises(FileExistsError):
        rd.write_decision(d)
    assert json.loads((rd.decisions_dir / "a_push_list.json").read_text())["decision"] == "hold"
```
Run -> ImportError. GREEN: `run_store_fs.py` implementing the protocol (`write_atomic`: `tempfile.NamedTemporaryFile(dir=target.parent, delete=False)` at 0600 -> `os.replace`; `write_decision`: `os.open(final, O_WRONLY|O_CREAT|O_EXCL, 0o600)` FIRST, then write the temp and `os.replace` over the reserved path - the O_EXCL reservation is what makes it write-once under concurrency). Run -> green.

- [ ] **Step 2: The `_git` stderr fix (adjacent rot, named in the M2 list).** In `adapters/graph_a/git_cli.py::_git`, catch `subprocess.CalledProcessError` and re-raise `RuntimeError(f"git {' '.join(args)} failed ({exc.returncode}): {exc.stderr.strip()}") from exc`. Add to `tests/test_graph_a_adapters.py`:
```python
def test_git_cli_errors_carry_git_stderr(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not a git repository|fatal"):
        GitCli().head_sha(tmp_path)
```
Check first that no existing test asserts `CalledProcessError` from `GitCli` (grep `CalledProcessError` under `tests/`); if one does, update it to the new type in the same commit and say so in the report.

- [ ] **Step 3: Gate and commit** by pathspec.

---

### Task 12: dispatch, replay and the coordinator context (the heart)

**Files:**
- Create: `src/agentdag/application/kernel/dispatch.py`, `src/agentdag/application/kernel/context.py`, `src/agentdag/application/kernel/workflow_check.py`
- Test: `tests/test_kernel_dispatch.py`, `tests/test_kernel_workflow_check.py`

**Interfaces:**
- Consumes: Tasks 9-11 (`NodeSpec`, `NodeOutcome`, `ResultRecord`, `journal_key`, `prefix_hash`, `content_hash`, `hash8`, `Journal`, `RunDir`, `Clock`, `stamp`, `build_replay_index`, `Suspended`, `NondeterministicCallError`).
- Produces:
  ```python
  # dispatch.py
  Body = Callable[[Path], Awaitable[NodeOutcome]]        # the node's body, handed its node dir
  @dataclass class Dispatcher:
      journal: Journal; run_dir: RunDir; clock: Clock; index: ReplayIndex   # index is rebuilt from journal.lines() at construction
      records: dict[str, ResultRecord]                    # node_id -> latest record, filled as the program runs (deps lookup)
      dispatched_keys: list[str]                          # this run's dispatch key sequence (the replay-purity oracle)
      async def dispatch(self, spec: NodeSpec, *, brief: str, input_obj: Mapping[str, Any], body: Body) -> ResultRecord
  # context.py
  class Coordinator:                                      # what a workflow program is handed
      run_id: str; workflow: str; args: BaseModel; dispatcher: Dispatcher; run_dir: RunDir; clock: Clock
      executors: Mapping[str, Executor]; gate_port: GatePort; git: GitPort; scanner: IsolationScanner; policy: Policy; parallel: int
      interactions: int = 0; tokens_by_row: dict[str, int] = {}                # both start empty in __init__
      async def work(self, spec, *, brief, cwd, prompt=DEFAULT_PROMPT) -> ResultRecord
      async def gate(self, spec, *, argv, cwd) -> ResultRecord
      async def scan(self, spec, *, watched: str) -> ResultRecord      # g_scan@i: the isolation-root scan as a gate node
      async def reduce(self, spec, *, fold: Callable[[], NodeOutcome]) -> ResultRecord
      async def map(self, map_id, items, body: Callable[[int, T], Awaitable[ResultRecord]]) -> list[ResultRecord]
      async def stage(self, spec, *, intents: Sequence[BaseModel], kind: str) -> ResultRecord
      async def approve(self, spec, *, payload: ApprovePayload) -> Decision   # raises Suspended
      async def apply(self, spec, *, intents, kind, perform: Callable[[BaseModel], str]) -> ResultRecord
      def snapshot(self) -> Manifest                                    # taken before a node for scan()
  # workflow_check.py
  def assert_deterministic(module: ModuleType) -> None                 # raises NondeterministicCallError naming the call and line
  ```
  Task 13 fills the primitive bodies (`gate`, `scan`, `reduce`, `map`, `stage`, `approve`, `apply`); this task builds `Dispatcher`, `Coordinator.work`, and the shells of the others raising `NotImplementedError` ONLY where Task 13 owns them (name each in a comment `# Task 13`), and the check.

**Out of scope** - do NOT touch, though they look related:
- The executor adapter (Task 14) - here the executor is a FAKE at the `Executor` port.
- The token cap and the budget refusal (M3): `charged_tokens` are summed into `RunState.tokens_by_row` here, nothing refuses.

**STOP conditions** - stop and report rather than improvise, if:
- the replay-purity test cannot be made to FAIL by a deliberate mutation (change the key field set and re-run: it must go red) - a test that cannot fail is decoration (design 9);
- you find yourself reading a record's prose (`message`, `text`) to branch: only `status`, `typed_fields`-named `key_facts` and `error.type` are branchable.

- [ ] **Step 1: RED - `tests/test_kernel_dispatch.py`** (fakes at the ports; the journal and run dir are the REAL adapters over `tmp_path`)

```python
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.dispatch import Body, Dispatcher
from agentdag.application.kernel.replay import build_replay_index
from agentdag.domain.models import Budget, ErrorType, Isolation, Kind, NodeOutcome, NodeSpec, NodeStatus


class TickingClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


def spec(node_id: str, deps: list[str] | None = None) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        kind=Kind.GATE,
        executor="code",
        isolation=Isolation.NONE,
        deps=deps or [],
        deadline_s=60,
        budget=Budget(),
    )


def done(**facts: object) -> NodeOutcome:
    return NodeOutcome(
        status=NodeStatus.DONE,
        key_facts=dict(facts),
        typed_fields=list(facts),
        executor_used="code",
        model_used="-",
        effort_used="-",
        artefact_refs=["x"],
    )


def make(tmp_path: Path) -> tuple[Dispatcher, JsonlJournal, FsRunDir]:
    rd = FsRunDir.create(tmp_path, "r")
    j = JsonlJournal(rd.journal_path, rd.audit_path)
    return Dispatcher.from_journal(journal=j, run_dir=rd, clock=TickingClock()), j, rd


def test_dispatch_writes_started_then_result_and_the_node_dir_holds_brief_input_and_record(tmp_path: Path) -> None:
    d, j, rd = make(tmp_path)
    calls: list[Path] = []

    async def body(node_dir: Path) -> NodeOutcome:
        calls.append(node_dir)
        return done(n=2)

    r = asyncio.run(d.dispatch(spec("g"), brief="scan", input_obj={"a": 1}, body=body))
    assert r.status == "done" and r.key_facts == {"n": 2} and r.duration_s >= 0
    kinds = [type(x).__name__ for x in j.lines()]
    assert kinds == ["StartedLine", "ResultLine"]
    nd = calls[0]
    assert (nd / "brief.md").read_text() == "scan" and (nd / "input.json").exists() and (nd / "record.json").exists()
    assert d.dispatched_keys == [j.lines()[0].key]


def test_replay_serves_the_record_without_running_the_body_and_reproduces_the_key_sequence(tmp_path: Path) -> None:
    d, j, rd = make(tmp_path)

    async def body(_: Path) -> NodeOutcome:
        return done(n=1)

    async def boom(_: Path) -> NodeOutcome:
        raise AssertionError("body must not run on replay")

    asyncio.run(d.dispatch(spec("a"), brief="b", input_obj={}, body=body))
    asyncio.run(d.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=body))
    replay = Dispatcher.from_journal(journal=j, run_dir=rd, clock=TickingClock())
    asyncio.run(replay.dispatch(spec("a"), brief="b", input_obj={}, body=boom))
    asyncio.run(replay.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=boom))
    journal_keys = [ln.key for ln in j.lines() if type(ln).__name__ == "StartedLine"]
    assert replay.dispatched_keys == journal_keys  # replay purity: same keys, same order, same length
    assert len(j.lines()) == 4  # zero new lines: zero dispatches


def test_a_changed_dep_result_changes_the_dependent_key(tmp_path: Path) -> None:
    d, j, rd = make(tmp_path)

    async def one(_: Path) -> NodeOutcome:
        return done(n=1)

    async def two(_: Path) -> NodeOutcome:
        return done(n=2)

    asyncio.run(d.dispatch(spec("a"), brief="b", input_obj={}, body=one))
    asyncio.run(d.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=one))
    k_b = d.dispatched_keys[1]
    d2, _, _ = make(tmp_path / "other")
    asyncio.run(d2.dispatch(spec("a"), brief="b", input_obj={}, body=two))
    asyncio.run(d2.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=one))
    assert d2.dispatched_keys[1] != k_b


def test_crash_window_is_redispatched_and_only_it(tmp_path: Path) -> None:
    d, j, rd = make(tmp_path)
    ran: list[str] = []

    def ok(node_id: str) -> Body:
        async def body(_: Path) -> NodeOutcome:
            ran.append(node_id)
            return done(n=1)

        return body

    async def crash(_: Path) -> NodeOutcome:
        raise SystemExit(9)  # the coordinator PROCESS dies mid-body: on disk that is a started line with no result.
        # SystemExit (like KeyboardInterrupt) is what asyncio propagates straight out of a task and
        # asyncio.run; any other BaseException would be STORED as the task's result and not crash anything.

    async def program(dispatcher: Dispatcher, third: Body) -> None:
        await dispatcher.dispatch(spec("n1"), brief="b", input_obj={}, body=ok("n1"))
        await dispatcher.dispatch(spec("n2", deps=["n1"]), brief="b", input_obj={}, body=ok("n2"))
        await dispatcher.dispatch(spec("n3", deps=["n2"]), brief="b", input_obj={}, body=third)

    with pytest.raises(SystemExit):
        asyncio.run(program(d, crash))
    assert [type(x).__name__ for x in j.lines()][-1] == "StartedLine"  # n3 started, no result: the crash window
    resumed = Dispatcher.from_journal(journal=j, run_dir=rd, clock=TickingClock())
    ran.clear()
    asyncio.run(program(resumed, ok("n3")))
    assert ran == ["n3"]  # exactly node 3 re-dispatched; n1, n2 served


def test_an_empty_or_junk_done_outcome_is_failed_agents_empty_result(tmp_path: Path) -> None:
    d, *_ = make(tmp_path)

    async def empty(_: Path) -> NodeOutcome:
        return NodeOutcome(status=NodeStatus.DONE, executor_used="code", model_used="-", effort_used="-")

    async def junk(_: Path) -> NodeOutcome:
        return NodeOutcome(
            status=NodeStatus.DONE, key_facts={"prose": "did it"}, executor_used="code", model_used="-", effort_used="-"
        )

    for body in (empty, junk):
        r = asyncio.run(d.dispatch(spec("e"), brief="b", input_obj={}, body=body))
        assert r.status == "failed" and r.error is not None and r.error.type == ErrorType.AGENTS_EMPTY_RESULT


def test_a_raising_body_is_a_failed_record_not_an_exception(tmp_path: Path) -> None:
    d, *_ = make(tmp_path)

    async def raising(_: Path) -> NodeOutcome:
        raise RuntimeError("clone failed")

    r = asyncio.run(d.dispatch(spec("c"), brief="b", input_obj={}, body=raising))
    assert (
        r.status == "failed"
        and r.error is not None
        and r.error.type == ErrorType.EXECUTOR_ERROR
        and r.error.transient is True
    )
```
Run -> ImportError.

- [ ] **Step 2: GREEN - `dispatch.py`**

```python
"""Dispatch: the ONE path every node takes (design 3.2). Replay is served here, the crash window is re-run here."""

from __future__ import annotations
import time  # noqa: F401 - NOT used; the clock is injected. Remove this line; it is here to remind you the check in Task 12 step 4 must fail on it.
```
(Do NOT keep that import; the point is that `workflow_check` guards WORKFLOW modules, and this module is kernel code that gets its time from `Clock`.)
```python
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ...domain.journal import ResultLine, StartedLine
from ...domain.keys import canonical_json, content_hash, hash8, journal_key, prefix_hash
from ...domain.models import ErrorType, NodeError, NodeOutcome, NodeSpec, NodeStatus, ResultRecord
from .ports import Clock, Journal, RunDir, stamp
from .replay import ReplayIndex, build_replay_index

__all__ = ["Body", "Dispatcher"]
Body = Callable[[Path], Awaitable[NodeOutcome]]


@dataclass
class Dispatcher:
    journal: Journal
    run_dir: RunDir
    clock: Clock
    index: ReplayIndex
    records: dict[str, ResultRecord] = field(default_factory=dict)
    dispatched_keys: list[str] = field(default_factory=list)

    @classmethod
    def from_journal(cls, *, journal: Journal, run_dir: RunDir, clock: Clock) -> Dispatcher:
        return cls(journal=journal, run_dir=run_dir, clock=clock, index=build_replay_index(journal.lines()))

    async def dispatch(self, spec: NodeSpec, *, brief: str, input_obj: Mapping[str, Any], body: Body) -> ResultRecord:
        prefix = prefix_hash([self.records[d] for d in spec.deps])  # KeyError = program bug: a dep never dispatched
        brief_hash = content_hash(brief)
        input_text = canonical_json(dict(input_obj))
        input_hash = content_hash(input_text)
        key = journal_key(spec, brief_hash=brief_hash, input_hash=input_hash, prefix=prefix)
        self.dispatched_keys.append(key)
        served = self.index.results.get(key)
        if served is not None:
            self.records[spec.node_id] = served
            return served  # replay: no started line, no body
        node_dir = self.run_dir.node_dir(spec.node_id, hash8(key))
        self.run_dir.write_atomic(str(node_dir.relative_to(self.run_dir.root) / "brief.md"), brief)
        self.run_dir.write_atomic(str(node_dir.relative_to(self.run_dir.root) / "input.json"), input_text)
        self.journal.append(StartedLine(key=key, node_id=spec.node_id, attempt=spec.attempt, at=stamp(self.clock)))
        started = self.clock.now()
        outcome = await _run_body(body, node_dir)
        outcome = _refuse_empty(outcome)
        record = ResultRecord(
            **outcome.model_dump(by_alias=False),
            node_id=spec.node_id,
            attempt=spec.attempt,
            input_hash=input_hash,
            duration_s=(self.clock.now() - started).total_seconds(),
        )
        self.run_dir.write_atomic(
            str(node_dir.relative_to(self.run_dir.root) / "record.json"),
            record.model_dump_json(by_alias=True, indent=1),
        )
        self.journal.append(ResultLine(key=key, record=record, at=stamp(self.clock)))
        self.records[spec.node_id] = record
        return record


async def _run_body(body: Body, node_dir: Path) -> NodeOutcome:
    try:
        return await body(node_dir)
    except Exception as exc:  # noqa: BLE001 - a raising branch is a FAILED RECORD, never a dead fleet (M1 leftover 8)
        return NodeOutcome(
            status=NodeStatus.FAILED,
            executor_used="-",
            model_used="-",
            effort_used="-",
            error=NodeError(type=ErrorType.EXECUTOR_ERROR, message=f"{type(exc).__name__}: {exc}", transient=True),
        )


def _refuse_empty(outcome: NodeOutcome) -> NodeOutcome:
    """Design 9 'empty result counted': a done outcome with no artefact refs AND no typed key fact is failed/agents_empty_result."""
    if (
        outcome.status is not NodeStatus.DONE
        or outcome.artefact_refs
        or any(k in outcome.key_facts for k in outcome.typed_fields)
    ):
        return outcome
    return outcome.model_copy(
        update={
            "status": NodeStatus.FAILED,
            "error": NodeError(
                type=ErrorType.AGENTS_EMPTY_RESULT, message="no artefact refs and no typed key_facts", transient=False
            ),
        }
    )
```
`SystemExit` is a `BaseException`, so the crash test's body propagates as designed (and `_run_body` catches `Exception` only - a real crash must not become a tidy failed record). Note the replay branch appends the key to `dispatched_keys` BEFORE checking the index, so the sequence is comparable to the journal's `started` keys. Run the dispatch tests -> green (fix the awkward test line). Commit by pathspec.

- [ ] **Step 3: `context.py`** - the `Coordinator` with `work` and `snapshot`, the other primitives as Task 13 shells:

```python
class Coordinator:
    """What a workflow program is handed: every effect goes through a primitive here, every primitive through dispatch."""

    DEFAULT_PROMPT = (
        "Apply the change described in your system prompt to this repository. Commit with a clear message. Do not push."
    )

    def __init__(
        self,
        *,
        run_id,
        workflow,
        args,
        dispatcher,
        run_dir,
        clock,
        executors,
        gate_port,
        git,
        scanner,
        policy,
        parallel,
    ) -> None: ...  # typed, keyword-only; sets interactions=0, tokens_by_row={}
    async def work(self, spec: NodeSpec, *, brief: str, cwd: Path, prompt: str = DEFAULT_PROMPT) -> ResultRecord:
        row = self.policy.resolve(spec)  # Task 15: tier_role/model -> (row alias, executor name)
        executor = self.executors[row.executor]
        request_input = {
            "cwd": str(cwd.relative_to(self.run_dir.root)),
            "prompt": prompt,
            "model": row.alias,
            "effort": spec.effort,
        }

        async def body(node_dir: Path) -> NodeOutcome:
            req = ExecutorRequest(
                node_dir=node_dir,
                cwd=cwd,
                brief=brief,
                prompt=prompt,
                model=row.alias,
                effort=spec.effort,
                max_turns=self.policy.max_turns,
                isolation_root=self.run_dir.root,
                write_set=tuple(spec.write_set),
                deny_bash=self.policy.deny_bash,
            )
            return await executor.run(req)

        record = await self.dispatcher.dispatch(
            spec.model_copy(update={"executor": row.executor, "model": row.alias}),
            brief=brief,
            input_obj=request_input,
            body=body,
        )
        self._charge(record)
        return record

    def _charge(self, record: ResultRecord) -> None:  # tokens_by_row += charged_tokens; M3 turns this into the refusal
        for row_name, n in record.charged_tokens.items():
            self.tokens_by_row[row_name] = self.tokens_by_row.get(row_name, 0) + n
```
Task 15 provides `Policy.resolve`; until then `Coordinator` takes a minimal `Policy` protocol declared in `ports.py`: `resolve(spec) -> ResolvedRow(alias: str, executor: str)`, `max_turns: int`, `deny_bash: tuple[str, ...]`, `version: str`, `tokens_per_row: Mapping[str, int]`. Put that protocol in `ports.py` NOW (Task 15 implements it over YAML; Task 12's tests use a one-row fake).

- [ ] **Step 4: RED then GREEN - `workflow_check.py` and `tests/test_kernel_workflow_check.py`**

```python
import types, textwrap, pytest
from agentdag.application.kernel.workflow_check import assert_deterministic
from agentdag.domain.errors import NondeterministicCallError


def module_from(source: str) -> types.ModuleType:
    m = types.ModuleType("wf")
    m.__file__ = "wf.py"
    exec(compile(textwrap.dedent(source), "wf.py", "exec"), m.__dict__)
    m.__source__ = textwrap.dedent(source)
    return m


@pytest.mark.parametrize(
    "call",
    [
        "time.time()",
        "time.monotonic()",
        "datetime.now()",
        "datetime.datetime.utcnow()",
        "random.random()",
        "uuid.uuid4()",
        "os.urandom(4)",
        "secrets.token_hex()",
    ],
)
def test_a_workflow_reaching_for_the_clock_or_randomness_fails_at_load(call: str) -> None:
    src = f"import time, datetime, random, uuid, os, secrets\nfrom datetime import datetime as dt\ndef program(co, args):\n    return {call}\n"
    with pytest.raises(NondeterministicCallError, match="line 4"):
        assert_deterministic(module_from(src))


def test_a_workflow_that_takes_time_from_the_coordinator_loads() -> None:
    assert_deterministic(module_from("def program(co, args):\n    return co.clock.now()\n")) is None
```
`assert_deterministic(module)`: read the source (`inspect.getsource(module)`, or `module.__source__` when set - the test seam), `ast.parse`, walk `ast.Call` nodes and match the callee's dotted name against `{"time.time", "time.monotonic", "time.perf_counter", "datetime.now", "datetime.utcnow", "datetime.datetime.now", "datetime.datetime.utcnow", "random.*", "uuid.uuid4", "uuid.uuid1", "os.urandom", "secrets.*"}` (build the dotted name from `ast.Attribute`/`ast.Name` chains; `random.*` and `secrets.*` match any attribute call on those names) and raise `NondeterministicCallError(f"{name}() at line {node.lineno} is unavailable in coordinator code: it breaks resume; take the value from a record or the coordinator's clock")`. Run -> green.

- [ ] **Step 5: Gate and commit** by pathspec.

---

### Task 13: the code primitives - gate, the isolation-root scan, reduce, map, stage, approve (minimal), apply

**Files:**
- Modify: `src/agentdag/application/kernel/context.py` (fill `gate`, `scan`, `reduce`, `map`, `stage`, `approve`, `apply`, `snapshot`)
- Create: `src/agentdag/domain/scan.py` (pure: `Manifest`, `diff_manifests`, `stray_paths`), `src/agentdag/adapters/kernel/isolation_scan.py` (`IsolationScanner.snapshot(root) -> Manifest`)
- Modify: `src/agentdag/adapters/graph_a/gate_make.py:47-61` (`FileLock(str(lock), timeout=self._timeout)` with `timeout: float = 3600` in the constructor and a `filelock.Timeout` -> a clear `RuntimeError` naming the lock path - the M1 leftover), `src/agentdag/application/kernel/ports.py` (`IsolationScanner` protocol)
- Test: `tests/test_kernel_primitives.py`, `tests/test_kernel_scan.py`; one test added to `tests/test_graph_a_adapters.py` for the gate timeout message

**Interfaces:**
- Consumes: Task 12's `Coordinator`, `Dispatcher`; `GatePort` (`MakeTestGate.run(worktree, log) -> int`); `GitPort` (`ref_sha`, `default_branch`, `push`, `has_commit`); `RunDir` (`intents_dir`, `marker`, `manifest_path`, `read_decision`, `write_atomic`); the domain `PushIntent`, `is_scratch_target` from `domain/graph_a.py`.
- Produces (all on `Coordinator`; every one is a `dispatch` with a code body):
  - `gate(spec, *, argv, cwd) -> ResultRecord`: runs `gate_port.run(cwd, node_dir/"gate.log")` with the argv-configured gate (the gate port carries the argv; `argv` is recorded in `input_obj` for the key); `done` on rc 0 else `failed` with `key_facts={"rc": rc}`, `typed_fields=["rc"]`, `artefact_refs=["<node dir rel>/gate.log"]`.
  - `snapshot() -> Manifest` and `scan(spec, *, watched: str, before: Manifest, write_set) -> ResultRecord`: `after = scanner.snapshot(root)`, `stray = stray_paths(diff_manifests(before, after), allowed=write_set + node's own dir + the run-root exception prefixes for its kind)`; `done` when empty else `failed` with `key_facts={"stray": [...]}`, `typed_fields=["stray"]`. Compared on CONTENT hash, so a mode-only change is not a finding.
  - `reduce(spec, *, fold: Callable[[], NodeOutcome]) -> ResultRecord`; the map manifest `manifest/<map_id>.json` is written by the reduce that closes a map (`{map_id, branches: [{index, node_id, key, status}], reduced_at, reducer_version: "1"}`).
  - `map(map_id, items, body) -> list[ResultRecord]`: `asyncio.gather(*(body(i, item) ...), return_exceptions=True)`; an exception from a BRANCH (not from dispatch, which already contains its body) becomes a synthetic `failed/executor_error` record for `f"{map_id}@{i}"` - so a fleet never dies of one branch (M1 leftover 8); bounded by `asyncio.Semaphore(self.parallel)`.
  - `stage(spec, *, intents, kind) -> ResultRecord`: writes `intents/<kind>/<dedup_key>.json` per intent BEFORE anything else (design 3.4), `key_facts={"count": n, "keys": [...]}`, `typed_fields=["count", "keys"]`, `artefact_refs=[the intent paths]`.
  - `approve(spec, *, payload) -> Decision`: validates `payload.default` names an option with `effect == "none"` (else `SpecRejected`); if the replay index (or `run_dir.read_decision(node_id)`, folded by `run.py` on relaunch) holds a decision -> returns it (and the node's record is `done` with `key_facts={"decision": id}`); else writes `nodes/<id>/<hash8>/payload.json`, journals nothing more, and raises `Suspended(node_id)`. `interactions += 1` when a HUMAN decision (token_id != "system") is folded - the run_summary field.
  - `apply(spec, *, intents, kind, perform: Callable[[intent], str]) -> ResultRecord`: per intent, `done/<kind>/<key>` marker exists -> `already-done`; else `perform(intent)` (the workflow supplies the effect: for graph A `_push_one` = M1's `_apply_one` logic, ref check then push) then `marker.touch()`; `key_facts={"outcomes": {...}}`.
- Where the workflow gets a `NodeOutcome` for reduce: it builds one with `NodeOutcome(status=DONE, key_facts=..., typed_fields=..., artefact_refs=[...], executor_used="code", model_used="-", effort_used="-")`.

**Out of scope** - do NOT touch, though they look related:
- Approve identity/auth/duplicates/timer/`decide_by` (M3); the stage/apply kill-between-intent-and-push negative test with a REAL push (M3, on top of the marker/ref logic built here); the deadline (M3).
- The bmk-tool-env host lease under `/run/lock/agentdag/` (M3): the gate keeps M1's `--lock` file.

**STOP conditions** - stop and report rather than improvise, if:
- the scan cannot see a file written by a Bash `tee` into a SIBLING worktree in the test (then the snapshot walk is wrong - it must cover the whole run root minus `journal.jsonl`, `audit.jsonl`, `state.json`, `lock`, `nodes/*/*/{record.json,transcript.jsonl,telemetry.jsonl}`);
- `approve` needs to block or poll: it must RAISE `Suspended` and the caller exits - if you feel the need to `await` a decision, stop.

- [ ] **Step 1: RED - `tests/test_kernel_scan.py`** (pure + adapter)

```python
from pathlib import Path
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.domain.scan import diff_manifests, stray_paths


def test_scan_flags_a_write_outside_the_write_set_but_not_a_mode_change_or_an_edit_inside(tmp_path: Path) -> None:
    (tmp_path / "wt/a").mkdir(parents=True)
    (tmp_path / "wt/b").mkdir(parents=True)
    (tmp_path / "wt/a/f.py").write_text("x")
    (tmp_path / "wt/b/g.py").write_text("y")
    before = IsolationScanner().snapshot(tmp_path)
    (tmp_path / "wt/a/f.py").write_text("changed")  # inside the write-set
    (tmp_path / "wt/b/g.py").chmod(0o755)  # mode only
    (tmp_path / "wt/b/stray.txt").write_text("tee'd here")  # a sibling worktree: the finding
    (tmp_path / "nodes/w@1/abcd1234").mkdir(parents=True)
    (tmp_path / "nodes/w@1/abcd1234/transcript.jsonl").write_text("{}")  # the node's own dir: allowed
    after = IsolationScanner().snapshot(tmp_path)
    changed = diff_manifests(before, after)
    assert stray_paths(changed, allowed=("wt/a/**", "nodes/w@1/abcd1234/**")) == ["wt/b/stray.txt"]
```
GREEN: `domain/scan.py`: `Manifest = dict[str, str]` (relative posix path -> `sha256:<hex>` of content), `diff_manifests(before, after) -> list[str]` (added or content-changed paths, sorted; deletions count too, as `-<path>`? no - a deletion inside the write set is fine and outside is a finding: include deleted paths in the diff and let `stray_paths` judge them by path), `stray_paths(changed, allowed: Sequence[str]) -> list[str]` using `fnmatch`-style `**` globs via `pathlib.PurePosixPath.full_match` (3.13+) - the repo floor is 3.12, so implement `_matches(path, pattern)` with `fnmatch.fnmatchcase` after translating `**` to `*` on the posix string AND checking prefix segments; adapter `IsolationScanner.snapshot(root)`: `os.walk` skipping `.git` directories entirely (git's own object churn is not a stray write) and the four run-control files, hashing content in 1 MiB chunks. Run -> green.

- [ ] **Step 2: RED - `tests/test_kernel_primitives.py`** (real journal/run dir/git over `tmp_path`, a committing FAKE executor is not needed here - these are code primitives; use the `Coordinator` with a fake `Policy` and no executors)

```python
import asyncio, sys
from pathlib import Path
import pytest
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.context import Coordinator
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.domain.errors import SpecRejected, Suspended
from agentdag.domain.graph_a import PushIntent
from agentdag.domain.models import (
    ApproveOption,
    ApprovePayload,
    Budget,
    Decision,
    Isolation,
    Kind,
    NodeOutcome,
    NodeSpec,
    NodeStatus,
)


class OneRowPolicy:
    version = "sha256:test"
    max_turns = 5
    deny_bash = ("git push",)
    tokens_per_row = {"sonnet": 10}

    def resolve(self, spec):
        from agentdag.application.kernel.ports import ResolvedRow

        return ResolvedRow(alias="sonnet", executor="claude")


def coordinator(tmp_path: Path, *, gate_rc: int = 0) -> tuple[Coordinator, FsRunDir]:
    rd = FsRunDir.create(tmp_path / "runs", "r1")
    j = JsonlJournal(rd.journal_path, rd.audit_path)
    co = Coordinator(
        run_id="r1",
        workflow="t",
        args={},
        dispatcher=Dispatcher.from_journal(journal=j, run_dir=rd, clock=UtcClock()),
        run_dir=rd,
        clock=UtcClock(),
        executors={},
        gate_port=MakeTestGate(
            lock=tmp_path / "gate.lock", command=(sys.executable, "-c", f"raise SystemExit({gate_rc})")
        ),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=OneRowPolicy(),
        parallel=2,
    )
    return co, rd


def code(node_id: str, kind: Kind, deps: list[str] = []) -> NodeSpec:
    return NodeSpec(
        node_id=node_id, kind=kind, executor="code", isolation=Isolation.NONE, deps=deps, deadline_s=60, budget=Budget()
    )


def test_gate_records_the_exit_code_and_the_log(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path, gate_rc=3)
    r = asyncio.run(co.gate(code("g_test@1", Kind.GATE), argv=("make", "test"), cwd=rd.root))
    assert r.status == NodeStatus.FAILED and r.key_facts["rc"] == 3 and (rd.root / r.artefact_refs[0]).exists()


def test_map_contains_a_raising_branch_and_still_returns_every_other_record(tmp_path: Path) -> None:
    co, _ = coordinator(tmp_path)

    async def body(i: int, item: str):
        if item == "bad":
            raise RuntimeError("clone exploded")
        return await co.reduce(
            code(f"m@{i}", Kind.REDUCE),
            fold=lambda: NodeOutcome(
                status=NodeStatus.DONE,
                key_facts={"i": i},
                typed_fields=["i"],
                artefact_refs=["x"],
                executor_used="code",
                model_used="-",
                effort_used="-",
            ),
        )

    records = asyncio.run(co.map("m", ["a", "bad", "c"], body))
    assert [r.status for r in records] == [NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.DONE] and records[
        1
    ].error is not None


def test_stage_writes_intents_before_apply_and_apply_is_idempotent(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    intents = [PushIntent(repo=Path("/s/origin/a.git"), head_sha="a" * 40, dedup_key="a.git-" + "a" * 40)]
    s = asyncio.run(co.stage(code("s", Kind.STAGE), intents=intents, kind="push"))
    assert (rd.intents_dir("push") / ("a.git-" + "a" * 40 + ".json")).exists() and s.key_facts["count"] == 1
    performed: list[str] = []
    a1 = asyncio.run(
        co.apply(
            code("ap", Kind.APPLY, deps=["s"]),
            intents=intents,
            kind="push",
            perform=lambda it: (performed.append(it.dedup_key), "pushed")[1],
        )
    )
    a2 = asyncio.run(
        co.apply(
            code("ap2", Kind.APPLY, deps=["s"]),
            intents=intents,
            kind="push",
            perform=lambda it: (performed.append(it.dedup_key), "pushed")[1],
        )
    )
    assert (
        performed == ["a.git-" + "a" * 40]
        and a1.key_facts["outcomes"] == {"a.git-" + "a" * 40: "pushed"}
        and a2.key_facts["outcomes"] == {"a.git-" + "a" * 40: "already-done"}
    )


def payload(default: str = "hold") -> ApprovePayload:
    return ApprovePayload(
        text="push?",
        node_id="a",
        artefact_refs=[],
        options=[
            ApproveOption(id="approve", label="push", effect="external"),
            ApproveOption(id="hold", label="hold", effect="none"),
        ],
        default=default,
        decide_by="2026-08-18T09:00:00+00:00",
        workflow="t",
        run_id="r1",
    )


def test_approve_suspends_without_a_decision_and_returns_it_when_one_is_journaled(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    with pytest.raises(Suspended) as info:
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))
    assert info.value.node_id == "a" and list(rd.root.glob("nodes/a/*/payload.json"))
    rd.write_decision(Decision(node_id="a", decision="approve", by="me", token_id="local"))
    co2, _ = coordinator(tmp_path / "again")
    co2.run_dir = rd
    co2.fold_decisions()  # what run.py does on relaunch
    assert (
        asyncio.run(co2.approve(code("a", Kind.APPROVE), payload=payload())).decision == "approve"
        and co2.interactions == 1
    )


def test_approve_refuses_a_default_with_an_external_effect(tmp_path: Path) -> None:
    co, _ = coordinator(tmp_path)
    with pytest.raises(SpecRejected):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(default="approve")))
```
(`co2.run_dir = rd` is clumsy - construct `co2` over the same run dir instead: give `coordinator()` an optional `rd` argument. Fix the helper when you write it.) `fold_decisions()` reads every `decisions/*.json` not yet in the replay index and journals an `ApproveDecisionLine` for each (`by`, `token_id` from the file), then rebuilds the index - it lives on `Coordinator` and `run.py` calls it first thing on a relaunch. Run -> ImportError.

- [ ] **Step 3: GREEN** - fill the primitives in `context.py` per Interfaces; `map` uses `asyncio.Semaphore(self.parallel)` and `return_exceptions=True`; the synthetic branch failure record is written THROUGH `dispatch` (a spec `f"{map_id}@{i}"`, kind `MAP`? no - `map` has no record of its own; a branch that raised outside any dispatch gets a `ResultRecord` built directly with `input_hash="-"` and appended to the returned list, NOT journaled, and the reduce's manifest marks it `failed`). Keep the gate's `argv` in `input_obj` so a different gate command is a different key. Add the `FileLock` timeout to `MakeTestGate` and this test to `tests/test_graph_a_adapters.py`:
```python
def test_gate_reports_a_held_lock_by_path_instead_of_hanging(tmp_path: Path) -> None:
    from filelock import FileLock

    with FileLock(str(tmp_path / "l")):
        gate = MakeTestGate(lock=tmp_path / "l", command=(sys.executable, "-c", "raise SystemExit(0)"), timeout=0.2)
        with pytest.raises(RuntimeError, match=str(tmp_path / "l")):
            gate.run(tmp_path, tmp_path / "g.log")
```
(FileLock is re-entrant within one process only for the SAME object; a second `FileLock` object on the same path blocks, so this test is real. If it does not block on your platform, use a subprocess holder.) Run everything -> green.

- [ ] **Step 4: Gate and commit** by pathspec.

---

### Task 14: the Claude executor - allowlisted env, per-node credential, PreToolUse hooks, tokens that mean what they say

**Files:**
- Create: `src/agentdag/adapters/kernel/executor_claude.py`, `src/agentdag/adapters/kernel/hooks_claude.py` (the two hook callables, pure functions of their input dict - unit-testable without the SDK)
- Modify: `src/agentdag/adapters/config/defaultconfig.toml` (a `[credentials]` table: `claude_oauth_token_file = ""` meaning "use the credential copy"; a `[kernel]` table: `runs_dir = "/var/lib/agentdag/runs"`, `parallel = 2`, `max_turns = 25`, `deny_bash = ["git push", "gh pr", "gh release", "curl -X POST", "curl --data", "wget --post"]`) - through the template's config mechanism, tests in `tests/test_cli_config.py` style show how a key is read
- Test: `tests/test_kernel_executor_claude.py` (hooks and env building; NO model call), the secrets grep test in `tests/test_kernel_secrets.py`

**Interfaces:**
- Consumes: `ExecutorRequest`, `NodeOutcome`, `Tokens`, `NodeError`, `ErrorType` (Tasks 9-10); the D3 measurement (Task 8) for the DEFAULT credential path; `claude-agent-sdk` `ClaudeAgentOptions(hooks=..., env=..., permission_mode="dontAsk", allowed_tools=[...], setting_sources=[], cwd, system_prompt, model, max_turns)`, `HookMatcher`, `ClaudeSDKClient`, `ResultMessage`, `AssistantMessage`.
- Produces: `ClaudeExecutor(credentials: CredentialSource, *, deny_bash, tools=DEFAULT_TOOLS)` implementing `Executor.run(request) -> NodeOutcome`; `CredentialSource` = `OAuthTokenFile(path)` (env `CLAUDE_CODE_OAUTH_TOKEN`, empty `CLAUDE_CONFIG_DIR` under `node_dir/home/.claude`) or `CredentialCopy(source_path)` (M1's copy into `node_dir/home/.claude/.credentials.json`, 0600, O_EXCL) - both expose `child_env(node_dir) -> dict[str, str]`; `hooks_claude.py`: `deny_outside_root(isolation_root: Path) -> HookCallback` (Write|Edit|MultiEdit|NotebookEdit: `realpath(tool_input["file_path"])` not under root -> `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": ...}}`), `deny_bash_commands(patterns) -> HookCallback` (substring match on `tool_input["command"]` after collapsing whitespace). Tokens: `Tokens(in=input+cache_creation+cache_read, out=output, cache_read=cache_read, reasoning=None)` from `ResultMessage.usage`; `charged_tokens={model: in+out}`; the record also carries `key_facts={"turns": n, "first_turn_input_tokens": k}`, `typed_fields=["turns"]`, `artefact_refs=[<cwd rel>]` - all built by one pure function `outcome_from_usage(*, model, num_turns, is_error, text, usage, first_turn_input, cwd_rel) -> NodeOutcome` that the tests drive without the SDK; `error.type = auth_failure` when the result text contains "Not logged in" or the CLI exits with the login error and `is_error`; `executor_error` (transient) on any exception; the raw messages are appended to `node_dir/transcript.jsonl` as they stream (scrubbed: any value of a key matching `(?i)token|secret|password|authorization|credential` replaced by `[scrubbed]`).

**Out of scope** - do NOT touch, though they look related:
- `adapters/graph_a/work_claude_sdk.py` (the baseline's adapter stays as it is; the kernel's is new code, sharing nothing but the idea).
- The token CAP call site (per-turn usage check + `interrupt()`) and the 3.8 handover nudge: M3, both at the streamed-usage seam this task creates - leave a named hook point: `_on_turn(usage: dict) -> None` called per `AssistantMessage`, doing nothing yet but recording `first_turn_input_tokens`.

**STOP conditions** - stop and report rather than improvise, if:
- the probe in Step 1 shows a node under `permission_mode="dontAsk"` with the hooks installed CANNOT edit a file inside its worktree, or a hook does NOT deny an outside path (then the enforcement design of design 7 is wrong for 0.2.139 - report the exact SDK behaviour, do not fall back to `acceptEdits` silently);
- `hooks` in `ClaudeAgentOptions` requires a signature different from `async def hook(input_data, tool_use_id, context) -> dict` (read `types.py` `HookCallback` and follow it; report the actual signature);
- the D3 probe found NEITHER credential path authenticates.

- [ ] **Step 1: PROBE FIRST (design rule: probe harness behaviour before building on it).** `workflow/probes/probe_hooks_dontask.py` in RESEARCH (PEP 723): one `ClaudeSDKClient` in a temp git worktree with `permission_mode="dontAsk"`, `allowed_tools=["Read","Edit","Write","Bash","Grep","Glob"]`, `setting_sources=[]`, `hooks={"PreToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit|NotebookEdit", hooks=[deny_outside_root]), HookMatcher(matcher="Bash", hooks=[deny_bash])]}`, the credential path Task 8 chose, and the prompt "Create the file INSIDE.txt with the word ok in the current directory, then try to create /tmp/agentdag-probe-OUTSIDE.txt, then run `git push` and report each result." Expected: INSIDE.txt exists, OUTSIDE does not, the transcript shows two denials with our reasons. Record MEASURED results in `workflow/design/probes/m2-hooks-dontask.md`. Cost: one sonnet turn. If the expected outcome does not hold, STOP.

- [ ] **Step 2: RED - `tests/test_kernel_executor_claude.py`** (hooks and env, no SDK process)

```python
import asyncio, json
from pathlib import Path
from agentdag.adapters.kernel.executor_claude import ClaudeExecutor, CredentialCopy, OAuthTokenFile
from agentdag.adapters.kernel.hooks_claude import deny_bash_commands, deny_outside_root


def decision(result: dict) -> str | None:
    return (result.get("hookSpecificOutput") or {}).get("permissionDecision")


def test_write_hook_denies_paths_outside_the_isolation_root_after_realpath(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "wt").mkdir()
    outside = tmp_path / "elsewhere.txt"
    (root / "wt" / "link").symlink_to(tmp_path)  # the symlink route out
    hook = deny_outside_root(root)
    assert (
        decision(
            asyncio.run(
                hook({"tool_name": "Write", "tool_input": {"file_path": str(root / "wt" / "a.py")}}, None, None)
            )
        )
        is None
    )
    assert (
        decision(asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": str(outside)}}, None, None)))
        == "deny"
    )
    assert (
        decision(
            asyncio.run(
                hook({"tool_name": "Edit", "tool_input": {"file_path": str(root / "wt" / "link" / "x")}}, None, None)
            )
        )
        == "deny"
    )
    assert (
        decision(
            asyncio.run(
                hook(
                    {"tool_name": "Write", "tool_input": {"file_path": str(root / "wt" / ".." / ".." / "escape")}},
                    None,
                    None,
                )
            )
        )
        == "deny"
    )


def test_bash_hook_denies_the_listed_commands_however_spaced() -> None:
    hook = deny_bash_commands(("git push", "gh pr"))
    assert (
        decision(
            asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "git   push origin main"}}, None, None))
        )
        == "deny"
    )
    assert (
        decision(
            asyncio.run(
                hook({"tool_name": "Bash", "tool_input": {"command": "git status && gh  pr create"}}, None, None)
            )
        )
        == "deny"
    )
    assert (
        decision(asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, None, None)))
        is None
    )


def test_child_env_is_an_allowlist_and_the_credential_never_touches_the_operator_file(tmp_path: Path) -> None:
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    node_dir = tmp_path / "node"
    node_dir.mkdir()
    env = OAuthTokenFile(keyfile).child_env(node_dir)
    assert (
        env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-SECRET"
        and Path(env["CLAUDE_CONFIG_DIR"]).is_dir()
        and not any(Path(env["CLAUDE_CONFIG_DIR"]).iterdir())
    )
    assert set(env) <= {
        "HOME",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "USERPROFILE",
        "SYSTEMROOT",
        "TMPDIR",
        "TEMP",
        "TMP",
    }
    src = tmp_path / "creds.json"
    src.write_text('{"t": 1}')
    env2 = CredentialCopy(src).child_env(tmp_path / "node2")
    copy = Path(env2["CLAUDE_CONFIG_DIR"]) / ".credentials.json"
    assert copy.read_text() == '{"t": 1}' and "CLAUDE_CODE_OAUTH_TOKEN" not in env2
    copy.write_text("refreshed")
    assert src.read_text() == '{"t": 1}'


def test_result_translation_sums_the_three_input_fields_and_names_auth_failure() -> None:
    from agentdag.adapters.kernel.executor_claude import outcome_from_usage

    o = outcome_from_usage(
        model="sonnet",
        num_turns=3,
        is_error=False,
        text="",
        usage={
            "input_tokens": 50,
            "cache_creation_input_tokens": 3873,
            "cache_read_input_tokens": 119786,
            "output_tokens": 1034,
        },
        first_turn_input=3923,
        cwd_rel="wt/r",
    )
    assert (
        o.tokens is not None
        and o.tokens.in_ == 50 + 3873 + 119786
        and o.charged_tokens == {"sonnet": 50 + 3873 + 119786 + 1034}
        and o.status == "done"
    )
    bad = outcome_from_usage(
        model="sonnet",
        num_turns=0,
        is_error=True,
        text="Not logged in - Please run /login",
        usage={},
        first_turn_input=0,
        cwd_rel="wt/r",
    )
    assert (
        bad.status == "failed"
        and bad.error is not None
        and bad.error.type == "auth_failure"
        and bad.error.transient is False
    )
```
Run -> ImportError. GREEN per Interfaces. `child_env` builds from an ALLOWLIST of the coordinator's environment (`PATH`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR`/`TEMP`/`TMP`, and on Windows `SYSTEMROOT`, `USERPROFILE`) plus `HOME=<node_dir>/home` and the credential keys - and because the SDK MERGES `env` over `os.environ` (M1 note), the coordinator PROCESS must itself be started with a clean environment (Task 17 does that in `run start`); this adapter additionally passes every OTHER inherited variable that looks like a secret as an explicit empty override (`{k: "" for k in os.environ if SECRET_KEY_RE.search(k)}`), so a leak needs both layers to fail. Run -> green.

- [ ] **Step 3: RED then GREEN - `tests/test_kernel_secrets.py`** (design 9 "secrets stay out", the mechanical form): after Task 16's end-to-end fake run exists, this test greps the whole run dir for the token prefixes `sk-ant-`, `oat01-`, `ghp_`, `pypi-` and requires zero hits; write it now against a run dir produced by the Task 13 primitives with a node whose brief CONTAINS `sk-ant-oat01-PLANTED` (so the scrub is exercised, not vacuous): the transcript scrubber must replace it, the brief.md keeps it (a brief is the operator's text) - the assertion is on `transcript.jsonl` and `record.json` only. State this scope in the test's docstring.

- [ ] **Step 4: Gate and commit** by pathspec (plus the probe files in RESEARCH by pathspec).

---

### Task 15: the policy table - YAML in, tier resolution and run limits out

**Files:**
- Create: `src/agentdag/domain/policy.py` (`TierRow`, `RunLimits`, `Policy` pydantic models + `resolve_row(policy, tier_role, model) -> TierRow`), `src/agentdag/adapters/kernel/policy_yaml.py` (`load_policy(path) -> LoadedPolicy` with `version = "sha256:" + sha256(bytes)`), `src/agentdag/policy/tier-policy.yaml` (copy of `workflow/design/schemas/tier-policy.example.yaml`)
- Modify: `pyproject.toml` (runtime dep `pyyaml`; hatch already ships non-py files under `src/agentdag/`, verify with `uv build && unzip -l dist/*.whl | grep policy`)
- Test: `tests/test_kernel_policy.py`

**Interfaces:**
- Consumes: the `Policy` protocol of Task 12 (`resolve(spec) -> ResolvedRow`, `max_turns`, `deny_bash`, `version`, `tokens_per_row`).
- Produces: `LoadedPolicy` implementing it: `resolve(spec)`: `spec.model` given -> the row with that alias must exist and be `available` and list `spec.tier_role` when a role is given (else `SpecRejected`); no model -> the cheapest AVAILABLE row listing the role by ascending `rank`; NO row -> `SpecRejected` (the one-role-down fallback and the `clamp` line are planner territory, later). `run_limits.tokens_per_row`, `thresholds.max_continuations`, `handover_at_tokens` per row are LOADED and exposed (M3 reads them); `max_turns` and `deny_bash` come from the app config (`[kernel]`), not the YAML.

**Out of scope** - do NOT touch, though they look related:
- Rules 2 (fallback), 4 (clamps), 5 (escalation) of design 2.3, `spec_rejected`/`clamp` journal lines: planner-driven, later.
- Resources beyond loading the list (`resources:` is parsed into models and otherwise unused in M2).

**STOP conditions** - stop and report rather than improvise, if:
- the example YAML does not parse into the models without a value change (then a design copy is wrong; report the key);
- pyyaml's `safe_load` is not enough (it is; `yaml.load` without a Loader is forbidden - never use it).

- [ ] **Step 1: RED - `tests/test_kernel_policy.py`**

```python
from importlib.resources import files
from pathlib import Path
import pytest
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.domain.errors import SpecRejected
from agentdag.domain.models import Budget, Isolation, Kind, NodeSpec, TierRole


def shipped() -> Path:
    return Path(str(files("agentdag.policy") / "tier-policy.yaml"))


def work(**over):
    base = dict(
        node_id="w",
        kind=Kind.WORK,
        executor="claude",
        tier_role=TierRole.STANDARD,
        isolation=Isolation.WORKTREE,
        deadline_s=60,
        budget=Budget(),
    )
    base.update(over)
    return NodeSpec.model_validate(base)


def test_shipped_policy_loads_and_is_versioned_by_content() -> None:
    p = load_policy(shipped())
    assert p.version.startswith("sha256:") and p.tokens_per_row["sonnet"] == 8_000_000
    assert p.rows["sonnet"].handover_at_tokens == 100_000 and p.thresholds.max_continuations == 3


def test_role_resolves_to_the_cheapest_available_row_and_a_model_override_is_checked() -> None:
    p = load_policy(shipped())
    assert (
        p.resolve(work()).alias == "sonnet" and p.resolve(work(tier_role=TierRole.DEEP)).alias == "codex"
    )  # rank 25 lists deep before opus at 30
    assert p.resolve(work(model="opus", tier_role=TierRole.DEEP)).alias == "opus"
    with pytest.raises(SpecRejected):
        p.resolve(work(model="opus"))  # opus does not list standard
    with pytest.raises(SpecRejected):
        p.resolve(work(model="nonesuch"))


def test_a_row_flipped_unavailable_is_skipped(tmp_path: Path) -> None:
    text = (
        shipped()
        .read_text()
        .replace(
            "alias: sonnet\n    executor: claude\n    rank: 20\n    cost_class: mid\n    available: true",
            "alias: sonnet\n    executor: claude\n    rank: 20\n    cost_class: mid\n    available: false",
        )
    )
    (tmp_path / "p.yaml").write_text(text)
    with pytest.raises(SpecRejected):
        load_policy(tmp_path / "p.yaml").resolve(work())  # standard is listed by sonnet only
```
(Check the exact indentation of the shipped YAML before writing the replace; the test must actually flip the row - assert `"available: false" in text` first.) Note the resolution result for `deep`: with the example table the Codex row (rank 25) is the cheapest listing `deep`; in M2 only `claude` executors exist, so `Coordinator.work` will raise `KeyError("mcp:codex/codex")` on a `deep` node - graph A uses `standard` only; document that a `deep` node needs an edited policy (the text used to say "M4 or an edited
policy"; M4 is cut, so an edited policy is the only answer). Run -> ImportError. GREEN. Then gate and commit by pathspec.

---

### Task 16: graph A on the kernel, end to end, replay and crash-window proven

**Files:**
- Create: `src/agentdag/application/workflows/__init__.py` (`WorkflowDef(name, args_model, program)`, `WORKFLOWS: dict[str, WorkflowDef]`, `get_workflow(name)` raising `WorkflowNotFound`), `src/agentdag/application/workflows/graph_a.py`
- Create: `src/agentdag/application/kernel/run.py` (`run_coordinator(...)`), `src/agentdag/application/kernel/summary.py` (`run_summary_line(...)` pure)
- Test: `tests/test_workflow_graph_a.py`, `tests/test_kernel_run.py`

**Interfaces:**
- Consumes: everything above; `domain/graph_a.py` (`parse_repos_text`, `reduce_tally`, `stage`, `dedup_key`, `is_scratch_target`, `Tally`, `PushIntent`); `GitPort`.
- Produces:
  ```python
  class GraphAArgs(BaseModel): repos_file: Path; brief_file: Path; scratch: Path; parallel: int = 2; model: str | None = None
  async def program(co: Coordinator, args: GraphAArgs) -> None      # the graph as code, node ids as in graphs/A-fleet-migration.md
  # run.py
  @dataclass(frozen=True) class RunOutcome: status: RunStatus; suspended_node: str | None; dispatched_keys: list[str]
  async def run_coordinator(*, run_dir: RunDir, journal: Journal, clock: Clock, lock: RunLock, workflow: WorkflowDef, args: BaseModel,
                            executors, gate_port, git, scanner, policy, parallel: int, by: str, token_id: str, resume_reason: str | None) -> RunOutcome
  ```
  `run_coordinator`: `lock.acquire(run_dir.root, current_holder())`; `assert_deterministic(workflow.module)`; if the journal is empty append `RunStartedLine` (args = `args.model_dump(mode="json")`, `by`, `token_id`, `policy.version`) else append `ResumeLine(reason=resume_reason or "manual")`; build `Dispatcher` and `Coordinator`; `co.fold_decisions()`; `state=running`; `await program(co, args)`; on `Suspended` -> `state=suspended`, `cursor=node_id`, return; on normal return -> `RunSummaryLine` appended, `state=done`; on any other exception -> `state=failed` (the exception is re-raised after the state write); `finally: lock.release`.
  Graph A program (node ids from the design's node table; N=2 in tests, any N in code):
  1. `g_discover` = `co.reduce(spec(node_id="g_discover", kind=Kind.GATE, executor="code", isolation=NONE, deadline_s=300), fold=...)` over `parse_repos_text(args.repos_file.read_text())` - the spec's kind is `gate` as the design table has it (it halts the run when the list is empty), and `co.reduce` is simply the primitive that runs a CODE fold through dispatch (the same path a `reduce` takes; there is one code-body path). `key_facts={"items": [str paths], "n": N}`, typed; the repos file's content hash goes into `input_obj` so an edited list is a different key. Refuses non-scratch targets and duplicate basenames like M1 (`SpecRejected` -> the run fails before any dispatch); `n == 0` -> the program returns and the run is `done` with nothing dispatched after it.
  2. `m_migrate` = `co.map("m_migrate", items, branch)`; per branch `i` with `name`: `before = co.snapshot()`; clone the origin into `co.run_dir.worktree(name)` (via `git.clone`; note it in `input_obj`); `w = await co.work(spec("w_migrate@i", kind WORK, tier_role standard, isolation worktree, write_set [f"wt/{name}/**"], deps ["g_discover"], deadline 3600, budget sonnet 400k, model=args.model), brief=brief, cwd=wt)`; if `w.status != done` -> the branch's tally is `work-failed`; else `t = await co.gate(spec("g_test@i", GATE, isolation dir, write_set [f"wt/{name}/**"], requires [bmk-tool-env 1], deps ["w_migrate@i"], deadline 1800), argv=("make","test"), cwd=wt)`; `s = await co.scan(spec("g_scan@i", GATE, deps ["w_migrate@i"]), watched=f"wt/{name}", before=before, write_set=[f"wt/{name}/**"])`; the branch returns the `Tally` (status passed iff `t` done AND `s` done; head sha via `git.head_sha(wt)` read AFTER the gate).
  3. `r_tally` = `co.reduce(spec("r_tally", REDUCE, deps [every branch's last node]), fold=...)` -> `reduce_tally(rows)` written to `artefacts/tally.json` and the map manifest; `key_facts={"passed_count", "failed_count", "skipped_count"}`, typed.
  4. route on `passed_count == 0` -> return (nothing staged; the run ends done).
  5. `s_push_intent` = `co.stage(spec, intents=stage(summary), kind="push")`.
  6. `a_push_list` = `co.approve(spec, payload=ApprovePayload(text=listing, node_id="a_push_list", artefact_refs=[...intent paths], options=[approve(external), hold(none)], default="hold", decide_by=(co.clock.now()+86400 s) formatted, workflow="graph-a", run_id=co.run_id))` -> `Decision`; on `hold`/`reject` -> return.
  7. `ap_push` = `co.apply(spec, intents, kind="push", perform=push_one)` where `push_one(intent)`: `is_scratch_target` or raise; `git.ref_sha(intent.repo, branch) == intent.head_sha` -> `"already-present"` else `git.push(worktree, intent.repo, branch)` -> `"pushed"` (M1's `_apply_one`, verbatim logic).

**Out of scope** - do NOT touch, though they look related:
- The Codex A/B arm (odd i) - M4; `w_migrate_codex@i` is not dispatched in M2.
- The token cap refusal, deadline, cancel - M3.

**STOP conditions** - stop and report rather than improvise, if:
- the crash-window end-to-end test cannot be made to re-dispatch EXACTLY the started-without-result node (that is D2's first re-open condition - report it, do not paper over it with a "close enough" assertion);
- `resume.py`-equivalent code (`run.py` + `replay.py`) plus `journal_jsonl.py` exceed 300 lines together - report the count (D2 re-open condition 2), do not trim to fit.

- [ ] **Step 1: RED - `tests/test_workflow_graph_a.py`** (real git, real journal/run dir, a COMMITTING FAKE at the `Executor` port, the interpreter as the gate command)

```python
import asyncio, subprocess, sys
from pathlib import Path
import pytest
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.ports import ExecutorRequest
from agentdag.application.kernel.replay import build_replay_index
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.workflows import get_workflow
from agentdag.application.workflows.graph_a import GraphAArgs
from agentdag.domain.models import Decision, NodeOutcome, NodeStatus, RunStatus, Tokens


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout.strip()


def make_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@example.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "Makefile").write_text("test:\n\t@exit 0\n")
    (repo / "README.md").write_text(f"# {name}\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    return repo


class CommittingExecutor:
    """The Claude node stand-in: edits a file in its cwd and commits, like the brief asks; optionally crashes the coordinator on a named node."""

    def __init__(self, crash_on: str | None = None) -> None:
        self.crash_on = crash_on
        self.calls: list[str] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        self.calls.append(request.node_dir.parent.name)
        if request.node_dir.parent.name == self.crash_on:
            raise SystemExit(9)  # the process dies: started on disk, no result
        (request.cwd / "CHANGELOG.md").write_text(request.brief + "\n")
        git("add", "-A", cwd=request.cwd)
        git("commit", "-q", "-m", "change", cwd=request.cwd)
        return NodeOutcome(
            status=NodeStatus.DONE,
            key_facts={"turns": 1},
            typed_fields=["turns"],
            artefact_refs=[str(request.cwd.relative_to(request.isolation_root))],
            tokens=Tokens(**{"in": 10, "out": 5, "cache_read": 0, "reasoning": None}),
            charged_tokens={request.model: 15},
            executor_used="claude",
            model_used=request.model,
            effort_used="-",
        )


class StrayExecutor(CommittingExecutor):
    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        (request.isolation_root / "wt" / "other" / "STRAY").parent.mkdir(parents=True, exist_ok=True)
        (request.isolation_root / "wt" / "other" / "STRAY").write_text("tee")
        return await super().run(request)


def fleet(tmp_path: Path, names: list[str], *, parallel: int) -> tuple[GraphAArgs, list[Path]]:
    scratch = tmp_path / "scratch"
    (scratch / "origin").mkdir(parents=True)
    origins = []
    for n in names:
        real = make_repo(tmp_path, n)
        GitCli().mirror(real, scratch / "origin" / f"{n}.git")
        origins.append(scratch / "origin" / f"{n}.git")
    (tmp_path / "REPOS.txt").write_text("".join(f"{o}\n" for o in origins))
    (tmp_path / "BRIEF.md").write_text("add a line")
    return GraphAArgs(
        repos_file=tmp_path / "REPOS.txt", brief_file=tmp_path / "BRIEF.md", scratch=scratch, parallel=parallel
    ), origins


def launch(tmp_path: Path, executor, *, run_id: str = "r1", resume: str | None = None, parallel: int = 2):
    rd = FsRunDir.open(tmp_path / "runs", run_id) if resume else FsRunDir.create(tmp_path / "runs", run_id)
    args = (
        GraphAArgs.model_validate(rd.read_state().args) if resume else fleet(tmp_path, ["a", "b"], parallel=parallel)[0]
    )
    return asyncio.run(
        run_coordinator(
            run_dir=rd,
            journal=JsonlJournal(rd.journal_path, rd.audit_path),
            clock=UtcClock(),
            lock=FileRunLock(),
            workflow=get_workflow("graph-a"),
            args=args,
            executors={"claude": executor},
            gate_port=MakeTestGate(lock=tmp_path / "gate.lock", command=(sys.executable, "-c", "raise SystemExit(0)")),
            git=GitCli(),
            scanner=IsolationScanner(),
            policy=load_policy(Path(__file__).parents[1] / "src/agentdag/policy/tier-policy.yaml"),
            parallel=args.parallel,
            by="tester",
            token_id="local",
            resume_reason=resume,
        )
    ), rd


def test_graph_a_suspends_at_the_approve_then_a_decision_resumes_it_to_a_push(tmp_path: Path) -> None:
    ex = CommittingExecutor()
    outcome, rd = launch(tmp_path, ex)
    assert (
        outcome.status == RunStatus.SUSPENDED
        and outcome.suspended_node == "a_push_list"
        and rd.read_state().status == RunStatus.SUSPENDED
    )
    assert sorted(ex.calls) == ["w_migrate@0", "w_migrate@1"]
    rd.write_decision(Decision(node_id="a_push_list", decision="approve", by="tester", token_id="local"))
    outcome2, _ = launch(tmp_path, ex, resume="decision")
    assert outcome2.status == RunStatus.DONE and sorted(ex.calls) == [
        "w_migrate@0",
        "w_migrate@1",
    ]  # zero re-dispatch of the work
    origin = tmp_path / "scratch" / "origin" / "a.git"
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "HEAD", cwd=rd.worktree("a"))
    assert git("rev-parse", "main", cwd=tmp_path / "a") != git(
        "rev-parse", "main", cwd=origin
    )  # the REAL repo is untouched
    idx = build_replay_index(JsonlJournal(rd.journal_path, rd.audit_path).lines())
    assert idx.crash_window == set() and idx.decisions["a_push_list"].by == "tester" and idx.run_started is not None
    summary = [
        ln for ln in JsonlJournal(rd.journal_path, rd.audit_path).lines() if type(ln).__name__ == "RunSummaryLine"
    ][-1]
    assert summary.human_interactions == 1 and summary.tokens_by_row == {"sonnet": 30}


def test_a_crash_between_started_and_result_resumes_by_redispatching_exactly_that_node(tmp_path: Path) -> None:
    # parallel=1 on purpose: with two branches in flight, the sibling's gate could be mid-thread (started, no result) at the
    # moment of the crash and the window would hold TWO keys by construction, not by defect. Serial branches make "exactly one" exact.
    ex = CommittingExecutor(crash_on="w_migrate@1")
    with pytest.raises(SystemExit):
        launch(tmp_path, ex, parallel=1)
    rd = FsRunDir.open(tmp_path / "runs", "r1")
    idx = build_replay_index(JsonlJournal(rd.journal_path, rd.audit_path).lines())
    assert len(idx.crash_window) == 1 and rd.read_state().status in (RunStatus.RUNNING, RunStatus.CRASHED)
    ex2 = CommittingExecutor()
    outcome, _ = launch(tmp_path, ex2, resume="crash")
    assert ex2.calls == ["w_migrate@1"] and outcome.status == RunStatus.SUSPENDED  # exactly the crashed node, once


def test_replay_of_a_finished_run_dispatches_nothing_and_reproduces_the_key_sequence(tmp_path: Path) -> None:
    ex = CommittingExecutor()
    launch(tmp_path, ex)
    rd = FsRunDir.open(tmp_path / "runs", "r1")
    rd.write_decision(Decision(node_id="a_push_list", decision="hold", by="tester", token_id="local"))
    launch(tmp_path, ex, resume="decision")
    lines_before = JsonlJournal(rd.journal_path, rd.audit_path).lines()
    started_keys = [ln.key for ln in lines_before if type(ln).__name__ == "StartedLine"]
    outcome, _ = launch(tmp_path, ex, resume="manual")
    lines_after = JsonlJournal(rd.journal_path, rd.audit_path).lines()
    assert outcome.status == RunStatus.DONE and len(ex.calls) == 2
    assert [ln for ln in lines_after if type(ln).__name__ == "StartedLine"] == [
        ln for ln in lines_before if type(ln).__name__ == "StartedLine"
    ]
    # replay purity: the same keys, the same count. The ORDER of two parallel map branches is scheduling-dependent by
    # construction (a real map, not a defect), so order is compared as a multiset here; within a chain the order is
    # enforced by the key itself (a dependent's key embeds its dep's record hash), and Task 12's serial test checks it exactly.
    assert (
        rd.read_state().cursor is None
        and sorted(outcome.dispatched_keys) == sorted(started_keys)
        and len(outcome.dispatched_keys) == len(started_keys)
    )


def test_a_stray_write_into_a_sibling_worktree_fails_the_scan_and_the_branch(tmp_path: Path) -> None:
    outcome, rd = launch(tmp_path, StrayExecutor())
    tally = (rd.root / "artefacts" / "tally.json").read_text()
    assert outcome.status == RunStatus.DONE and '"passed": 0' in tally  # nothing pushable, nobody asked
```
Run -> ImportError.

- [ ] **Step 2: GREEN** - `workflows/__init__.py`, `workflows/graph_a.py`, `kernel/run.py`, `kernel/summary.py` per Interfaces. `run_summary_line`: `tokens_by_row` from the coordinator, `journal_lines`/`journal_bytes` from the files, `records_per_node` = result lines / distinct node ids, `replay_seconds` = the wall time of `build_replay_index` on a resume (`clock` before/after) else `None`, `human_interactions` = `co.interactions`, `overhead_fraction` median/p90 over records whose `key_facts` carry `first_turn_input_tokens` and `tokens.in` (`(first_turn - brief_tokens_estimate) / in`; the estimate is `len(brief)/4`, stated in the docstring as the estimate it is; `{"median": 0, "p90": 0}` when no record qualifies), `citation_coverage=[]` (no synth in graph A). Run -> green. Then the deliberate mutation of the STOP condition: temporarily remove `"attempt"` from `_IDENTITY_FIELDS` and confirm `test_journal_key_ignores_limits...` goes red; put it back.

- [ ] **Step 3: Count the lines** `wc -l src/agentdag/application/kernel/run.py src/agentdag/application/kernel/replay.py src/agentdag/adapters/kernel/journal_jsonl.py` and write the number into the report (D2 re-open condition 2: 300).

- [ ] **Step 4: Gate and commit** by pathspec.

---

### Task 17: the scope, the CLI over the run dir, the composition root

**Files:**
- Create: `src/agentdag/adapters/kernel/scope_systemd.py` (`SystemdScope`: `start` = `systemd-run --user --scope --unit=<unit> --collect <argv>` via `subprocess.Popen` with the given `env`/`cwd`, returns `ScopeHandle(unit, pid)`; `is_alive` = `systemctl --user is-active <unit>` == `active`; `kill` = `systemctl --user stop <unit>` then poll `/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/app.slice/<unit>` for `cgroup.procs` EMPTY or the dir GONE, up to 10 s -> `True`, else `False`), `src/agentdag/adapters/kernel/scope_none.py` (`NoScope`: plain `Popen`, `is_alive` = `pid` poll, `kill` = SIGTERM then SIGKILL, `True` when `poll()` is not None), `src/agentdag/adapters/cli/commands/run.py`, `src/agentdag/composition/kernel.py`
- Modify: `src/agentdag/adapters/cli/root.py` (register `cli_run`), `src/agentdag/composition/__init__.py` + `src/agentdag/application/ports.py` (a `WireKernel` protocol and `wire_kernel` in `AppServices`, exactly as `wire_graph_a` is done), `src/agentdag/application/kernel/ports.py` (`KernelWiring` dataclass: `journal_factory`, `lock`, `clock`, `executors`, `gate_port`, `git`, `scanner`, `policy`, `scope`, `runs_dir`, `parallel`)
- Test: `tests/test_cli_run.py` (CliRunner with a services factory whose `wire_kernel` hands back the fakes, like `tests/test_cli_graph_a.py::services_wiring`), `tests/test_kernel_scope.py` (`os_linux` + `local_only`; plus an `os_agnostic` test of `NoScope`)

**Interfaces:**
- Consumes: Task 16's `run_coordinator`, `get_workflow`; Task 10's `Scope`; the app config keys of Task 14 (`[kernel] runs_dir, parallel, max_turns, deny_bash`, `[credentials] claude_oauth_token_file`).
- Produces the CLI:
  - `agentdag run start WORKFLOW [--arg key=value]... [--runs DIR] [--parallel N] [--policy FILE] [--foreground]`: validates args through the workflow's `args_model` (a bad `--arg` -> `INVALID_ARGUMENT` with pydantic's message), mints `run_id`, `FsRunDir.create`, writes `state.json` (`status=running`, `owner` = login name, `args`), then EITHER `--foreground`: `run_coordinator` in-process (the testable path; exit code 0 for `done`/`suspended`, and prints `run <id> suspended at <node>` or `run <id> done`), OR default: `scope.start(unit=f"agentdag-run@{run_id}", argv=[sys.executable, "-m", "agentdag", "run", "_coordinate", run_id, "--runs", ...], env=CLEAN_ENV, cwd=run_dir)` where `CLEAN_ENV` is the same allowlist Task 14 uses (`PATH`, `HOME`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR`, Windows' `SYSTEMROOT`/`USERPROFILE`) - the coordinator process is started WITHOUT the operator's secrets in its environment - and prints `run <id> started (unit ...)`. `_coordinate` is a hidden subcommand = the foreground path for an existing run dir with `resume_reason=None`.
  - `agentdag run status RUN_ID [--runs DIR]`: prints `state.json` fields (status, cursor, tokens_by_row) and the last journal event; exit 0.
  - `agentdag run records RUN_ID [--runs DIR] [--json]`: one line per `result` line (node_id, attempt, status, charged tokens) or the records as JSON.
  - `agentdag run resume RUN_ID [--runs DIR] [--foreground] [--reason decision|crash|restart|manual]`: refuses (`INVALID_ARGUMENT`) when `state.status` is `done`; else relaunches like `start` with `resume_reason`.
  - `agentdag run approve RUN_ID NODE_ID --decision ID [--reason TEXT] [--runs DIR] [--no-relaunch]`: reads the node's `payload.json` (from `nodes/<node>/*/payload.json`, the newest by the journal not mtime - there is exactly one hash dir per attempt; if several, refuse and say so), validates `ID` is one of `options[].id`, writes `decisions/<node>.json` (`by` = login name, `token_id: local`) - `FileExistsError` -> `INVALID_ARGUMENT "already decided"`; then relaunches with `--reason decision` unless `--no-relaunch`.
  - exit codes via `lib_cli_exit_tools` as the other commands do; every error message names the run dir.
- Composition: `wire_kernel(*, runs, policy_path, credential, parallel, max_turns, deny_bash) -> KernelWiring` choosing `SystemdScope` on Linux when `shutil.which("systemd-run")` and `systemctl --user is-system-running` does not fail outright, else `NoScope` (and printing which at `run start`).

**Out of scope** - do NOT touch, though they look related:
- `agentdag run cancel` (M3: needs the verified cgroup-empty path); the deadline (M3); the timer unit for approve defaults (M3); the MCP surface (L1).
- `adapters/cli/commands/graph_a.py` (the baseline command stays as it is).

**STOP conditions** - stop and report rather than improvise, if:
- `systemd-run --user --scope` fails on the Linux dev host for the coordinator process (S0 measured it works for `sleep`; if it does not for our argv, report the exact stderr, do not fall back to `NoScope` silently on Linux);
- the CLI needs to read the token file to relaunch (it must not: the credential is read by the EXECUTOR at the call site inside the coordinator process, never by the CLI).

- [ ] **Step 1: RED - `tests/test_cli_run.py`** (foreground path, fakes at the executor and gate ports through the services factory)

Move `CommittingExecutor`, `git`, `make_repo` and `fleet` from Task 16's test module into `tests/kernel_fakes.py` and import them from both files. The injection follows `tests/test_cli_graph_a.py::services_wiring` exactly: build `AppServices` from `build_production()` with `wire_kernel` replaced by a closure returning a `KernelWiring` whose executor is the fake, then `cli_runner.invoke(cli_mod.cli, [...], obj=lambda: services)`.

```python
from __future__ import annotations
import re, sys
from pathlib import Path
from typing import TYPE_CHECKING
import pytest
from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.scope_none import NoScope
from agentdag.application.kernel.ports import KernelWiring
from agentdag.composition import AppServices, build_production
from tests.kernel_fakes import CommittingExecutor, fleet, git

if TYPE_CHECKING:
    from collections.abc import Callable
    from click.testing import CliRunner

POLICY = Path(__file__).parents[1] / "src/agentdag/policy/tier-policy.yaml"


def services_with(executor: CommittingExecutor, tmp_path: Path) -> Callable[[], AppServices]:
    wiring = KernelWiring(
        journal_factory=JsonlJournal,
        lock=FileRunLock(),
        clock=UtcClock(),
        executors={"claude": executor},
        gate_port=MakeTestGate(lock=tmp_path / "gate.lock", command=(sys.executable, "-c", "raise SystemExit(0)")),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=load_policy(POLICY),
        scope=NoScope(),
        runs_dir=tmp_path / "runs",
        parallel=2,
    )
    prod = build_production()
    services = AppServices(
        get_config=prod.get_config,
        get_default_config_path=prod.get_default_config_path,
        deploy_configuration=prod.deploy_configuration,
        display_config=prod.display_config,
        send_email=prod.send_email,
        send_notification=prod.send_notification,
        load_email_config_from_dict=prod.load_email_config_from_dict,
        init_logging=prod.init_logging,
        wire_graph_a=prod.wire_graph_a,
        wire_kernel=lambda **_: wiring,
    )
    return lambda: services


def start_args(tmp_path: Path) -> list[str]:
    args, _ = fleet(tmp_path, ["a", "b"], parallel=2)
    return [
        "run",
        "start",
        "graph-a",
        "--arg",
        f"repos_file={args.repos_file}",
        "--arg",
        f"brief_file={args.brief_file}",
        "--arg",
        f"scratch={args.scratch}",
        "--runs",
        str(tmp_path / "runs"),
        "--foreground",
    ]


@pytest.mark.os_agnostic
def test_run_start_foreground_suspends_then_approve_relaunches_and_pushes(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    (tmp_path / "runs").mkdir()
    ex = CommittingExecutor()
    obj = services_with(ex, tmp_path)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    assert started.exit_code == 0, started.output
    m = re.search(r"run (\S+) suspended at a_push_list", started.output)
    assert m, started.output
    run_id = m.group(1)
    status = cli_runner.invoke(cli_mod.cli, ["run", "status", run_id, "--runs", str(tmp_path / "runs")], obj=obj)
    assert status.exit_code == 0 and "suspended" in status.output and "a_push_list" in status.output
    records = cli_runner.invoke(cli_mod.cli, ["run", "records", run_id, "--runs", str(tmp_path / "runs")], obj=obj)
    for node in (
        "g_discover",
        "w_migrate@0",
        "w_migrate@1",
        "g_test@0",
        "g_test@1",
        "g_scan@0",
        "g_scan@1",
        "r_tally",
        "s_push_intent",
    ):
        assert re.search(rf"{re.escape(node)}\s+0\s+done", records.output), records.output
    origin = tmp_path / "scratch" / "origin" / "a.git"
    before = git("rev-parse", "main", cwd=origin)
    approved = cli_runner.invoke(
        cli_mod.cli,
        [
            "run",
            "approve",
            run_id,
            "a_push_list",
            "--decision",
            "approve",
            "--runs",
            str(tmp_path / "runs"),
            "--foreground",
        ],
        obj=obj,
    )
    assert approved.exit_code == 0 and f"run {run_id} done" in approved.output, approved.output
    assert git("rev-parse", "main", cwd=origin) != before and git("rev-parse", "main", cwd=tmp_path / "a") != git(
        "rev-parse", "main", cwd=origin
    )
    assert sorted(ex.calls) == ["w_migrate@0", "w_migrate@1"]  # the relaunch replayed the work
    again = cli_runner.invoke(
        cli_mod.cli,
        [
            "run",
            "approve",
            run_id,
            "a_push_list",
            "--decision",
            "approve",
            "--runs",
            str(tmp_path / "runs"),
            "--no-relaunch",
        ],
        obj=obj,
    )
    assert again.exit_code == ExitCode.INVALID_ARGUMENT and "already decided" in again.output


@pytest.mark.os_agnostic
def test_run_start_refuses_a_missing_runs_dir_and_a_bad_arg(cli_runner: CliRunner, tmp_path: Path) -> None:
    obj = services_with(CommittingExecutor(), tmp_path)
    args = start_args(tmp_path)
    args[args.index("--runs") + 1] = str(tmp_path / "nope")
    missing = cli_runner.invoke(cli_mod.cli, args, obj=obj)
    assert missing.exit_code == ExitCode.INVALID_ARGUMENT and str(tmp_path / "nope") in missing.output
    (tmp_path / "runs").mkdir()
    bad = cli_runner.invoke(cli_mod.cli, [*start_args(tmp_path), "--arg", "parallel=zero"], obj=obj)
    assert bad.exit_code == ExitCode.INVALID_ARGUMENT and "parallel" in bad.output
    assert not list((tmp_path / "runs").iterdir())  # a refused start creates no run dir


@pytest.mark.os_agnostic
def test_run_resume_refuses_a_done_run(cli_runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    obj = services_with(CommittingExecutor(), tmp_path)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    run_id = re.search(r"run (\S+) suspended", started.output).group(1)
    cli_runner.invoke(
        cli_mod.cli,
        [
            "run",
            "approve",
            run_id,
            "a_push_list",
            "--decision",
            "hold",
            "--runs",
            str(tmp_path / "runs"),
            "--foreground",
        ],
        obj=obj,
    )
    resumed = cli_runner.invoke(
        cli_mod.cli, ["run", "resume", run_id, "--runs", str(tmp_path / "runs"), "--foreground"], obj=obj
    )
    assert resumed.exit_code == ExitCode.INVALID_ARGUMENT and "done" in resumed.output
```
(`cli_runner` is the fixture `tests/conftest.py` already provides for the other CLI tests - check its name there and use that.) Run -> failures. GREEN per Interfaces. `run records` prints one aligned line per result: `<node_id> <attempt> <status> <charged tokens>`; `--json` dumps the records list.

- [ ] **Step 2: `tests/test_kernel_scope.py`**: `@pytest.mark.os_agnostic` - `NoScope().start(...)` on `[sys.executable, "-c", "import time; time.sleep(30)"]` is alive, `kill` returns `True` and the pid is gone; `@pytest.mark.os_linux @pytest.mark.local_only` - the same with `SystemdScope`, asserting the unit is `active` then that `kill` verified the cgroup EMPTY (read `cgroup.procs`, not the stop verb's return). Run locally: green (the CI skips the second).

- [ ] **Step 3: Gate and commit** by pathspec; then update `README.md` (a `## Coordinator (agentdag run)` section: the five verbs, the run dir layout, `/var/lib/agentdag/runs` setup as ONE documented command `sudo install -d -m 0700 -o "$USER" -g "$USER" /var/lib/agentdag/runs`, that Codex and the cap are not in this version) and `CHANGELOG.md` (`## [Unreleased]`: the kernel), version stays `0.0.1` until the user says otherwise (the release decision is theirs; the PyPI description now matches what ships).

---

### Task 18: the attended M2 run, the crash by hand, the note, the PR

**Files:**
- Create: `workflow/design/probes/m2-kernel.md` (RESEARCH)
- Modify: `DECISIONS.md` (item 6 -> DONE with the numbers; the M3 pointer)
- The `agentdag` PR from `feat/kernel` to `main`

**Interfaces:**
- Consumes: the whole of M2 on `feat/kernel`, gate green, CI green on the PR (ubuntu/windows/macos 3.12-3.14 - the same STOP as M1: no merge on red).
- Produces: MEASURED numbers for M3's inputs: wall time and tokens per branch on the kernel vs M1's `m1-baseline.md` (same two scratch repos, same trivial brief and the same `RET` chore brief), the crash-window resume BY HAND (kill -9 the coordinator process between a `started` and its `result` - watch `journal.jsonl` with `tail -f`; then `agentdag run resume <id> --reason crash`; count the re-dispatched nodes from the journal: exactly one), replay of the finished run (`run resume --reason manual`: zero new `started` lines), the systemd unit visible in `systemctl --user list-units 'agentdag-run@*'` while it runs and gone after, the secrets grep over the run dir (`grep -rIl -e sk-ant- -e oat01- /var/lib/agentdag/runs/<id>` -> nothing), the `resume`+`journal` line count, and everything that broke.

**Out of scope** - do NOT touch, though they look related:
- The real repos: scratch clones only, as M1 (`agentdag graph-a scratch` still makes them; the kernel's `repos_file` is that `REPOS.txt`).
- Releasing to PyPI (the user's call; note it in the handover).

**STOP conditions** - stop and report rather than improvise, if:
- the hand crash-window resume re-dispatches more or fewer than one node (D2 re-open condition 1);
- CI red on any OS after two fix rounds (report; the merge waits);
- a token prefix shows up in the run dir grep (a secrets leak is a STOP, then a fix, then a re-run).

- [ ] **Step 1: The runs.** With the operator's login: `agentdag run start graph-a --arg repos_file=/tmp/agentdag-scratch/REPOS.txt --arg brief_file=/tmp/agentdag-brief.md --arg scratch=/tmp/agentdag-scratch --arg parallel=2` (trivial brief), watch, approve with `agentdag run approve <id> a_push_list --decision approve`, verify both scratch origins advanced and both real repos unchanged. Then the `RET` chore brief from `m1-baseline.md`, timed. Then the crash: start a third run, `kill -9` the coordinator's pid (from `systemctl --user status agentdag-run@<id>`) while a `w_migrate@i` is `started`, resume, count. Then the replay. Then the grep.

- [ ] **Step 2: Write `workflow/design/probes/m2-kernel.md`** with the tables (the M1 note is the template), the D2 re-open check (both conditions, with the numbers), and "what broke".

- [ ] **Step 3: The PR** (`gh pr create` from `feat/kernel`, title `M2: the kernel - journal, replay, run dir, primitives, claude executor, graph A on the coordinator`, body from a file, no AI attribution), CI watched by head sha AND workflow name, the whole-branch review dispatched (opus) with the M1 review as the pattern, fix rounds, merge on green + Approved. Update `DECISIONS.md` item 6 and the ledger.

- [ ] **Step 4: Commit** the note and the handover in RESEARCH by pathspec.

---

## M3 - the three properties (Tasks 19-27)

Written 2026-08-20 against the MERGED M2 code (`agentdag` `main` `e89a1bf`), not against the plan
that produced it: every signature quoted below was read out of that tree. The mid plan's M3 section
governs scope; the four user decisions of 2026-08-20 (`DECISIONS.md` "Decided" and item 6b) govern
what is built and what is deliberately not.

**Branch:** one branch off `main`, `feat/kernel-m3`, as M2 used `feat/kernel`; one PR at the end.

**M3 GLOBAL CONSTRAINTS** (added to the plan-wide ones above, which still bind):

- The gate is `cd <agentdag-worktree> && env -u VIRTUAL_ENV BMK_PYTHON_CMD=$PWD/.venv/bin/python make
  test > /tmp/agentdag-<task>.log 2>&1; echo RC=$? >> /tmp/agentdag-<task>.log`, and the RC is read
  FROM THE LOG, never from the compound command's own exit status.

  NO CALLER-SIDE LOCK. An earlier version of this constraint wrapped the gate in
  `flock /run/lock/bmk-tool-env.lock` after two concurrent gates corrupted each other's verdicts on
  2026-08-20. bmk 3.17.0 now does it properly and the wrapper is retired: `bmk_toollock.py` holds a
  SHARED lock on bmk's machine-wide tool env for every bmk process's lifetime and takes it EXCLUSIVE
  only around the upgrade, and `venv.py` takes an exclusive lock scoped to the venv SYNC alone. Both
  mutations are guarded at the right granularity - readers do not exclude readers, so two repos gate
  concurrently, which our whole-gate `flock` forbade. Keeping it would now be a machine-wide stall
  that buys nothing. The Makefile enforces the floor (`BMK_MIN 3.17.0`), so an older bmk cannot
  silently reintroduce the race.

  `make test` still MUTATES the working tree (it regenerates the Makefile and can raise dependency
  floors), so commit with a PATHSPEC and a `-F` message file.
- pyright strict, ruff, import-linter (domain < application < adapters < composition), Google
  docstrings, `__all__`. Reproduce the CI axes locally before pushing:
  `.venv/bin/pyright --pythonpath .venv/bin/python --pythonversion 3.12` and again with
  `--pythonplatform Windows` and `--pythonplatform Darwin` - the `--pythonpath` is NOT optional.
- Tests import sibling helpers by BARE module name (`from kernel_fakes import ...`), never
  `from tests.kernel_fakes import ...`: bmk runs `python -m pytest` (cwd on `sys.path`) and CI runs
  bare `pytest`, and only the bare spelling works in both.
- Every new negative test is MUTATION-CHECKED: break the code it guards, see it fail, restore.
- No AI attribution anywhere; ASCII only.

### Task 19: the `Sandbox` port - DEFINITION ONLY, no container adapter

**Deferred by the user 2026-08-20:** the container adapter is PARKED. This task defines the port
and ships the `none` adapter (which is exactly today's behaviour), so the seam exists and every
later task is written against it. The container adapter is a later task, not this one.

**The trap this task must avoid** (stated by the user's own earlier objection to a one-adapter
seam): a port designed against `none` alone will fit `none` alone. So the Protocol is designed
against what a CONTAINER adapter needs - a mount list, an env, a network policy, the node dir and
the worktree - even though nothing implements it here. Write the container adapter's requirements
into the docstring as the reason each field exists.

**Files:**
- Create: `src/agentdag/application/kernel/sandbox.py` (the `Sandbox` Protocol, `SandboxRequest`,
  `SandboxGuarantees`)
- Create: `src/agentdag/adapters/kernel/sandbox_none.py` (`NoSandbox`)
- Modify: `src/agentdag/application/kernel/ports.py` (re-export, `KernelWiring` gains `sandbox`)
- Modify: `src/agentdag/composition/kernel.py` (wire `NoSandbox`)
- Modify: `src/agentdag/application/kernel/context.py` (record the guarantees on every dispatch)
- Modify: `src/agentdag/domain/models.py` (`ResultRecord.sandbox: SandboxGuarantees | None`)
- Modify: `schemas/result-record.schema.json` (+ the RESEARCH copy under
  `workflow/design/schemas/`), `README.md`, `CHANGELOG.md`
- Test: `tests/test_kernel_sandbox.py`, and the record-schema conformance test already in
  `tests/test_kernel_domain.py`

**Interfaces:**
- Produces:
  ```python
  class SandboxGuarantees(BaseModel):
      """What a sandbox adapter actually enforces for one node - journaled per node.

      A port whose adapters differ silently in what they enforce is worse than no port:
      every later claim about a run becomes ambiguous ("was that node contained?").
      So an adapter DECLARES, the coordinator records the declaration on the result
      record, and `agentdag run records` can answer the question from the run itself.
      """

      model_config = ConfigDict(frozen=True, extra="forbid")
      adapter: str  # "none" | "container" | "vm"
      filesystem: bool  # can the node reach paths outside its mounts?
      network_egress: bool  # is egress restricted to an allowlist?
      separate_uid: bool  # does the node run as another uid?


  @dataclass(frozen=True)
  class SandboxRequest:
      """One node's isolation request - the fields a CONTAINER adapter needs.

      `none` uses only `cwd` and `env`; the rest exist because the container adapter
      cannot be added later without them, and a port that has to change shape to gain
      its second adapter was never a port.
      """

      node_dir: Path  # the node's own store dir (writable)
      worktree: Path | None  # the repo it may write (writable), None for a code node
      isolation_root: Path  # the run root; nothing above it is ever mounted
      cwd: Path
      env: Mapping[str, str]
      network_allow: tuple[str, ...]  # hosts an egress policy may permit; () = no policy


  class Sandbox(Protocol):
      def guarantees(self) -> SandboxGuarantees: ...
      @contextmanager
      def prepare(self, request: SandboxRequest) -> Iterator[SandboxRequest]:
          """Yield the request as the EXECUTOR should use it (paths/env possibly rewritten)."""
  ```
- `NoSandbox.prepare` yields its argument unchanged and `guarantees()` returns
  `SandboxGuarantees(adapter="none", filesystem=False, network_egress=False, separate_uid=False)` -
  all three False, stated plainly: it enforces nothing, which is the honest declaration.

**Out of scope** - do NOT touch, though they look related:
- Any container/podman/nspawn code, image, or dependency - PARKED by the user's decision.
- `hooks_claude.py` and `isolation_scan.py` - they are the layers ABOVE the boundary and are
  unchanged by defining it.
- The executor's env allowlist (`build_options_env`) - the sandbox does not replace it.

**STOP conditions** - stop and report rather than improvise, if:
- wiring `sandbox` into `KernelWiring` would force a change to `Executor.run`'s signature (it
  should not: the coordinator prepares, the executor consumes a request it already accepts);
- the guarantees field cannot be added to `ResultRecord` without breaking the schema conformance
  test on the M2 runs' journals (they carry no `sandbox` field - it must be optional).

- [x] **Step 1: RED - `tests/test_kernel_sandbox.py`**

Three tests: `NoSandbox.guarantees()` declares all three False (a control that it does not claim
containment); `prepare` yields the request unchanged; and a dispatch through the coordinator writes
`sandbox.adapter == "none"` onto the result record.

- [x] **Step 2: the port, the adapter, the wiring, the record field**
- [x] **Step 3: schema + docs** - the record schema gains an optional `sandbox` object; the README
  section "What the kernel enforces, and what it does not" gains one sentence: the port exists, the
  shipped adapter enforces nothing, and a record says so per node.
- [x] **Step 4: gate, mutation-check, commit**
- [x] **DONE 2026-08-20** (`31b6568`, then `07a307c` and `8974537`). Recorded here 2026-08-27; the
  boxes above stayed open while the work landed. Two things the plan did not anticipate, both worth
  reading before extending the port:
  (a) `07a307c` - the first cut stamped `sandbox.guarantees()` onto the record `_dispatch` RETURNED,
  after `_run_and_record` had already written `record.json` and appended the journal's result line,
  so nothing persisted carried the field AND a served record was re-stamped with whatever adapter
  the CURRENT launch happened to be wired with. The stamp moved to record CONSTRUCTION
  (`Dispatcher.dispatch` threads the guarantees through `_run_and_record` into `_complete`); a
  served record now comes back from the journal untouched. Its regression tests read `record.json`
  and the journal line back off disk rather than asserting on the returned object.
  (b) `8974537` - the module docstring and `Sandbox.prepare`'s own both stated in the present tense
  that the coordinator calls `prepare()` and folds it into the `ExecutorRequest`. Nothing calls it
  outside `tests/test_kernel_sandbox.py`: `Coordinator.work` builds its request with no reference to
  the wired sandbox, and `_dispatch` only reads `guarantees()`. Both docstrings now say plainly that
  the per-dispatch half of the port is DEFINED and has no call site yet, and that a coordinator
  integration is what a container adapter will add. So `prepare` still has a definition and no
  consumer - that is the state the parked container adapter arrives into.

---

### Task 20: the token cap - per node at the turn seam, per run before dispatch

**Files:**
- Modify: `src/agentdag/adapters/kernel/executor_claude.py` (`_run`, `_on_turn` -> a real check)
- Modify: `src/agentdag/application/kernel/dispatch.py` (run-level refusal before a dispatch)
- Modify: `src/agentdag/application/kernel/context.py` (`_charge`: rebuild `tokens_by_row` from
  zero per launch, never add to a served record twice)
- Test: `tests/test_kernel_executor_claude.py`, `tests/test_kernel_dispatch.py`

**Interfaces:**
- Consumes: `NodeSpec.budget.tokens` (`dict[str, int]`, row alias -> cap), `RunLimits.tokens_per_row`
  (already parsed by `load_policy`), `input_total(usage)` (public, sums input + cache creation +
  cache read - the three fields a cap MUST sum, measured in M1).
- Per node: `_on_turn` needs the live client to stop a dispatch, which M2's docstring already
  flags. Change the seam to `_on_turn(usage, client)` (or hold the client on the instance for the
  duration of `_run`) and call `await client.interrupt()` once the running total passes the row's
  cap. Overshoot is bounded by ONE TURN and that fact goes in the record: `key_facts["cap_hit"] =
  True` plus `error = NodeError(type=ErrorType.BUDGET_EXCEEDED, message=..., transient=False)`,
  status `FAILED`.
- Per run: before `_run_and_record`, if `state.tokens_by_row[row] + <the node's cap>` would exceed
  `run_limits.tokens_per_row[row]`, refuse the dispatch: a `failed` record with
  `ErrorType.BUDGET_EXCEEDED`, no executor call. The refusal is a RECORD (the always-a-record
  invariant), not an exception.

**Out of scope:**
- The USD cap (was deferred to M4; **M4 IS CUT, so this is ORPHANED** - rows with a null price
  pair cannot derive one, and nobody owns it. Deferred-to-nothing reads as scheduled, which is why
  it is labelled here rather than left).
- Codex's charge-at-dispatch model (same: deferred to a milestone that no longer exists; it dies
  with the arm rather than being owed to anyone).

**STOP conditions:**
- `client.interrupt()` does not exist or does not stop the stream on SDK 0.2.142 (probe it FIRST,
  in `workflow/probes/`, and report the measured behaviour before building on it);
- the cap would have to be enforced by counting tokens the SDK does not stream per turn.

- [x] **Step 1: PROBE first** - DONE 2026-08-20, `workflow/probes/probe_interrupt.py`, note
  `workflow/design/probes/m3-interrupt.md`. The STOP condition does NOT fire: `interrupt()` exists
  on SDK 0.2.142, ended the stream 0.55 s after the call, and the dispatch still produced a
  terminal `ResultMessage` carrying usage. TWO REQUIREMENTS THE PROBE ADDS TO THIS TASK, because
  an interrupted dispatch is indistinguishable from a finished one by its own terminal message
  (`is_error: false`, `subtype: "success"`, EMPTY result text):
  (a) the cap must record the interruption ITSELF, on the path that called `interrupt()` - nothing
  downstream can recover the fact;
  (b) without that, a node capped at a TURN BOUNDARY is recorded as a plain SUCCESS - `done`, no
  error - because `outcome_from_usage(is_error=False)` gives a work node `artefact_refs=[cwd_rel]`
  and `_refuse_empty` returns early on any non-empty `artefact_refs` (EXERCISED, not read). Its
  half-finished worktree then feeds every downstream node as if the work were complete. This is a
  correctness hazard, not a labelling one, and it is the strongest reason the cap must stamp its
  own record.
  SECOND ARM, mid-tool (where the cap will actually fire): the stream stops even faster (0.088 s)
  and the tool's own child process DIES with it (measured by a ticking file, not a pgrep; ONE bash
  process, so whether a tool with DESCENDANTS - `make test` spawning pytest - is fully reaped is
  unmeasured, and this task must settle it) - but
  the terminal message then reports `is_error: true`, `subtype: "error_during_execution"`, the
  OPPOSITE of arm 1. So (c) the mid-tool case lands on the executor's error path as
  `EXECUTOR_ERROR` with `transient=True`, which is exactly what Task 24's retry path re-dispatches:
  a node stopped for exceeding its ceiling would be retried into spending it again. Stamp the
  budget stop `transient=False` on the path that called `interrupt()`, BEFORE any error
  classification runs, and test that a capped node is not retried.
- [x] **Step 2: RED** - a fake client whose stream yields three turns with known usage; the cap
  must stop it at turn 2, and the control (cap above the total) must let all three through.
- [x] **Step 3: implement, both call sites**
- [x] **Step 4: the accounting fix** - `tokens_by_row` REBUILT from the served records on each
  launch, with a test that two launches of the same run report the same totals (M2 added them).
- [x] **Step 5: gate, mutation-check, commit**
- [x] **DONE 2026-08-20** (`a788ec8`, then `384a1b6`), plus nine review fixes the same day
  (`534ca19`, `13060ab`, `be5d4d3`, `14893fb`, `1621eb8`, `c05070e`, `ea5ab6b`, `7104c46`,
  `36961e4`). Recorded here 2026-08-27; the boxes above stayed open while the work landed. Both
  call sites are built as specified: the per-node cap at the executor's turn seam
  (`_on_turn` -> `client.interrupt()`, latching `cap_hit`, stamping `BUDGET_EXCEEDED` with
  `transient=False` on its own path) and the per-run refusal before dispatch
  (`context.py:_run_cap_refusal`, a record and no executor call). `384a1b6` is the correction that
  the per-node cap tracks the dispatch's RUNNING spend, not one turn's context.
- [x] **A LATER DEFECT IN THIS TASK'S ARITHMETIC, and one wrong fix for it, both recorded because
  the plan cannot show what the cap actually counted.** The running sum added EVERY
  `AssistantMessage` stream event. The CLI emits one such event per CONTENT BLOCK, each repeating
  that request's own `message_id` and its usage, so one API request was charged once per block.
  Measured on five stored dispatches under the run store: 19 events over 12 distinct message ids,
  and 10/6, 24/16, 41/23, 26/17 - an inflation of 1.50x to 1.78x, with the distinct-id count equal
  to `num_turns` in four of the five. The live cost: a node holding about 250000 tokens read as past
  its 400000 cap and was interrupted with correct, finished work in its tree, which the run then
  discarded unexamined. Fixed 2026-08-22 in `dbb5c9e` by keying the sum on `message_id`, so a
  request counts once however many blocks it arrives in.
  The wrong fix first: `4785e17` (with `62a7d9e`) read the same 1.6x gap as "the record charges the
  terminal snapshot while the cap enforces the sum" and changed what `charged_tokens` records.
  `2dc6196` reverted both - the premise was false, `charged_tokens` was already the dispatch's
  spend, and the change wrote the double-counted figure into the record while asserting the refuted
  mechanism as measured fact in four docstrings, the port contract and the CHANGELOG. The tell that
  killed it was a prediction of its own that the data refuted: the gap should grow with turn count,
  and it does not (28 turns 1.78x, 6 turns 1.67x).

---

### Task 21: the deadline and `run cancel`, both verified by an empty cgroup

**Files:**
- Modify: `src/agentdag/application/kernel/run.py` (the deadline owner), `src/agentdag/adapters/cli/commands/run.py` (`cancel`)
- Create: `src/agentdag/application/kernel/cancel.py` if `run.py` would pass ~250 code lines
- Test: `tests/test_kernel_run.py`, `tests/test_cli_run.py`, `tests/test_kernel_scope.py`

**Interfaces:**
- `NodeSpec.deadline_s` exists and is clamped by `RunLimits.deadline_ceiling_s`; the coordinator
  kills the SCOPE when it passes and records `CANCELLED` with `ErrorType.DEADLINE`.
- `agentdag run cancel RUN_ID [--runs DIR]`: writes the cancel intent, returns AT ONCE (mcp-surface
  O25), and the journal line records `verified: true` only once `Scope.kill` confirms the cgroup is
  empty - `SystemdScope.kill` already polls exactly that.
- The startup sweep: `run start`/`resume` stop a scope left behind by a dead coordinator before
  starting a new one.

**Out of scope:** the approve timer (Task 22); the `Notifier` (Task 23).

**STOP conditions:** a cancel cannot be verified because the run used `NoScope` (state that in the
journal line as `verified: false` with the reason; do not claim what was not confirmed).

**THE AMBIGUITY THIS TASK MUST RESOLVE FIRST, stated here because it produced Task 20's defect in
its own form.** The mid plan says "the scope is killed at `deadline_s`". That is right for a RUN
deadline and WRONG for a NODE deadline, and `deadline_s` is a per-node field
(`domain/models.py:188`, `node-spec.schema.json` requires it per node). The coordinator runs INSIDE
the scope, so killing the scope to stop one node kills the coordinator, every sibling branch in
flight, and the run. Two mechanisms, and this task builds both without conflating them:

* A NODE deadline is enforced at the same seam as the token cap: the executor's turn loop, calling
  `client.interrupt()`, then stamping the record ITSELF (`CANCELLED` or `FAILED` with
  `ErrorType.DEADLINE`, `transient=False`, no `artefact_refs`) - because the probe measured that an
  interrupted dispatch reports itself as a plain success at a turn boundary and as an executor
  error mid-tool, so neither SDK shape may decide the outcome. Reuse `_budget_outcome`'s shape;
  do not duplicate it. A node deadline needs a wall-clock check per turn, so the turn seam now
  carries two ceilings - say in the docstring which is which, since Task 20 has already shown what
  happens when two quantities share one comparison.
* A RUN deadline (and `run cancel`) kills the SCOPE, which is the coordinator and everything under
  it, and is verified by the cgroup emptying - `SystemdScope.kill` already polls exactly that.

**What already exists, so this task extends rather than invents:** `RunDir` reserves
`<node_id>.cancel.json` (per-node) and `_run.cancel.json` (whole-run) and its decision readers
already EXCLUDE both (`run_store_fs.py:338-352`, `ports.py:236`), so the file names and the
exclusion are settled; `SystemdScope.kill` polls `cgroup.procs` empty or the cgroup gone with a
budget matched to design C8; `NoScope.kill` signals the process group on POSIX and one process on
Windows. Nothing reads `deadline_s` yet - it is required by the schema and consumed nowhere.

- [x] **Step 1: RED for the verified-cancel property.** A fake scope whose cgroup never empties must
  produce `verified: false` WITH the reason, never a bare `true`. Its control: a scope that does
  empty produces `verified: true`. This is the property that must not be faked, so write it first.
- [x] **Step 2: the node deadline at the turn seam**, with a control that a node finishing inside
  its deadline is untouched, and a test that the record says `deadline` rather than
  `agents_empty_result` or `executor_error` for BOTH SDK shapes (Task 20's tests are the pattern).
- [x] **Step 3: `agentdag run cancel RUN_ID`** - write `_run.cancel.json`, return AT ONCE
  (mcp-surface O25: the command does not wait for the kill), and journal `cancel {verified: bool}`
  once the scope confirms. Under `NoScope` on Windows, `verified: false` with the reason, per the
  STOP condition.
- [x] **Step 4: the startup sweep** - `run start` and `run resume` stop a scope left behind by a
  dead coordinator before starting a new one. The M2 crash probe measured the shape this has to
  handle: the executor children outlived the killed coordinator by about 40 seconds before exiting
  on their own stdin EOF, so a sweep that only checks "is the unit gone" can see a unit that is
  still draining.
- [x] **Step 5:** gate under the lock, mutation-check each negative test, commit.
- [x] **DONE 2026-08-20** (`1df4acc` the node deadline at the turn seam, `deef8d3` `run cancel` plus
  the startup sweep), with three review fixes the same day. Recorded here 2026-08-27; the boxes above
  stayed open while the work landed. The task's own ambiguity resolved as written - a NODE deadline
  is enforced at the token cap's seam and stamps its own record, a whole-run cancel kills the scope
  and is verified by the cgroup emptying. The three fixes:
  `b91a2cb` - `SystemdScope` normalises a reconstructed handle's unit name.
  `8dddfa4` - `sweep_stale_scope` called `scope.kill` and DISCARDED its return value, so in exactly
  the still-draining case Step 4 was written against the sweep proceeded silently and the caller
  launched a fresh coordinator into a unit name that may still be occupied, which `systemd-run`
  refuses outright. It now returns a bool, and both call sites (`_run_foreground` and `_relaunch`)
  check it and exit with a clear message rather than claiming a sweep that did not confirm.
  `234742c` - `WHOLE_RUN_NODE_ID` (`"_run"`) names `cancel_line.node_id` for a whole-run cancel, but
  nothing stopped a workflow declaring a real node with that id; `_validate_node_id` now reserves it,
  with a control that an ordinary id merely shaped like it (`_run_of_the_mill`) still works.

---

### Task 22: approve - identity, the timer default, and the duplicate refusal

**WHAT ALREADY EXISTS, read from the shipped tree 2026-08-20 - this task extends, it does not invent:**

- `ApprovePayload.decide_by` is written on every approve payload and read by NOTHING. `graph_a.py`'s
  `_decide_by` derives it from the run's own `run_started` line rather than the clock, and its
  docstring says why in a sentence this task must not break: the payload's content hash IS the
  approve node's dispatch identity, so a deadline read from `now` would move on every launch and
  re-dispatch an approve the journal already holds. A timer that applies a default must therefore
  NOT recompute `decide_by`; it reads the payload's own field.
- The external-effect refusal is ALREADY BUILT (`context.py::_validate_default`, design 2.4): a
  payload whose default option has `effect == "external"` is refused when the approve node runs.
  Keep it and keep its test; this task adds the timer that may rely on it, not the rule.
- Identity today is `by=getpass.getuser()` and `token_id="local"` at two call sites in
  `commands/run.py`. That is not authentication and this task does not make it one - the sandbox
  that would give it meaning is parked, and any process of the same OS account can write a decision.

**THE DECISION THIS TASK MUST TAKE FIRST: what runs the timer.** A systemd user timer is one
answer, and it is the one the mid plan names - but it puts the applying code OUTSIDE any run's
lock, which is the same shape as `run cancel`, and Task 21 has just shown what that costs (the
external path must reacquire the lock, and it kills rather than winds down). The alternatives are a
check inside `run resume`/`run start` (no new mechanism, but a default only applies when somebody
runs a command) and a coordinator-side check at the suspend point (impossible today - the
coordinator EXITS at a suspend). Decide with the reviewer's eye on it and write the reasoning down;
do not just build the timer because the plan said timer.

**Files:** `src/agentdag/adapters/cli/commands/run.py`, `src/agentdag/application/kernel/` (the
applying path, beside `cancel.py` which is the closest existing shape), a unit file under `deploy/`
IF the timer wins (a file the operator installs, never something the code installs itself),
`tests/test_cli_run.py`, `tests/test_kernel_primitives.py`

**Interfaces:** at `decide_by` the default is applied and journalled with `by: system` and a
`token_id` naming the timer rather than a user; the decision file is written by the SAME
write-once, temp-then-link path a human decision uses, so a human answering at the same moment
cannot be overwritten and the loser is refused rather than silently dropped.

**Out of scope:** a token file with hashed scopes (the server, L1); making `by`/`token_id`
authentication.

- [x] **Step 1: RED for the race that matters** - a human decision and the timer's default arriving
  for the SAME (node, payload hash). Exactly one must win, the other must be refused, and the
  journal must say which. This is the property most likely to be got wrong, so write it first.
- [x] **Step 2:** the applying path, taking the run lock the way `resolve_cancel` does.
- [x] **Step 3:** the timer (or the alternative chosen above), with its unit file if any.
- [x] **Step 4:** the two approve refusal branches M2 left untested, plus a test that the timer
  does NOT recompute `decide_by` (mutate it to use the clock and watch the approve node re-dispatch).
- [x] **Step 5:** gate, mutation-check each negative test, commit.
- [x] **DONE 2026-08-20** (`e27d82f`). Recorded here 2026-08-27; the boxes above stayed open while
  the work landed. **The decision this task had to take first came out as EXTERNAL PASS**, and the
  reasoning is in the new `application/kernel/approve.py` module docstring: a check inside
  `run resume`/`run start` needs no new mechanism but is not an OWNER, because a default would then
  apply only when somebody runs a command, which is exactly the case `decide_by` exists to cover;
  and a coordinator-side check at the suspend point cannot exist, since the coordinator has already
  exited. So `agentdag run apply-deadlines` drives one pass over every run in a runs directory,
  taking each run's lock the way `resolve_cancel` does, and the systemd user timer that calls it
  ships under `deploy/` for the OPERATOR to install - nothing installs anything itself.
  Task 21's cost did NOT transfer: applying a default is not a kill, it is the same write-once
  temp-then-link decision write a human's `run approve` performs, so the Step 1 race resolves on the
  filesystem's own atomic link - exactly one wins, the loser is refused, and the journal carries one
  decision rather than two.
  One consequence the plan did not name: the applied decision carries `by=system`, `reason=deadline`
  and a `token_id` naming the timer, which moved the run summary's "was a human involved" question
  off `token_id` (now the applying AGENT) and onto `by` (the identity the schema reserves). Keyed on
  the old field, every unattended default would have counted as a human interaction.
  `f905d04` is on this branch but is NOT this feature: `tests/test_kernel_sandbox.py` still built its
  gate with a `lock=` argument `MakeTestGate` dropped when bmk 3.17.0 began guarding its own shared
  tool environment, so that file errored and the suite was red before this task's work started.
  Adjacent rot, fixed on the way.

---

### Task 23: the `Notifier` port and its sinks

**Files:** `src/agentdag/application/kernel/notify.py`, `src/agentdag/adapters/kernel/notify_none.py`,
`src/agentdag/adapters/kernel/notify_mail.py` (through the repo's existing `btx_lib_mail` adapter),
composition, `tests/test_kernel_notify.py`

**Interfaces:** something OUTSIDE the graph - NEVER a node - emits a typed `run_event` on
`suspended` (carrying the payload summary and `decide_by`), `done`, `failed`, `crashed`. The mail
sink is the OPERATOR's channel, so it does not go through stage/apply.

This line used to name the emitters as "the coordinator and the approve timer", and building it
settled that. **The COORDINATOR emits `suspended`, `done` and `failed`, because those are its three
exits** (`application/kernel/run.py::_drive`, via `_announce` and `_announce_suspend`). **`crashed`
is the exit that writes nothing, so nobody inside can emit it**: it gets a DETECTOR,
`application/kernel/crash.py::record_crash`, run by `agentdag run apply-deadlines`. The approve
timer is not an emitter at all - `application/kernel/approve.py` holds no reference to the
`Notifier`. Corrected 2026-08-27.

**Negative test (mid plan):** a run that suspends with the mail sink configured produces EXACTLY
one notification, and a relaunch sends none.

- [x] **DONE 2026-08-21** (`e40f8ae`). RED, port, two sinks, wiring, gate, commit; CI green. The
  files landed as planned plus `application/kernel/crash.py`, which the plan did not name: the
  emitters are the coordinator's three EXITS and `crashed` is the exit that writes nothing, so it
  needed a detector rather than an emit site. `run apply-deadlines` runs it. See the mid plan's M3
  section for the emitter split and the three-fact crash rule, and
  `agentdag/EXECUTION-USER-REVIEW.md` for the calls made without asking.
  Six mutations were run against the new tests and all six were caught.
- [x] **Two follow-ups the same day, both about the operator rather than the run** (recorded here
  2026-08-27). `9b5b0e2` - a run CONTAINS whatever its sink raises, deliberately, because a mail
  server being down is not a run failure; the cost is that a MISCONFIGURED sink behaves exactly like
  no sink and nothing in a run ever says so. `notify-test` is the one place that failure is not
  contained. It resolves the sink through the same function a run does (`resolve_notifier`, made
  public for it) so it cannot report healthy what `run start` would refuse, and with no sink
  configured it says so and exits 0, that being a correct answer rather than a failure. Its limit is
  worth carrying forward: it proves the sink worked once, now.
  `41e54a7` - `notify-test` exited `SMTP_FAILURE`, which is true only while mail is the only sink
  that can fail; the plan anticipates a client push sink, and an operator debugging a failed push
  would have read "SMTP" and gone to look at their mail server. It is `GENERAL_ERROR` now, with the
  cause left where it always was, in the sink's own error text. The test asserts the exact code
  rather than merely non-zero - an exit code is a contract somebody scripts against.

---

### Task 24: the retry path - a failed CODE node can be re-attempted

**Why this exists:** review B measured it - a transient failure of a code node (gate, scan, tally,
discover) is journaled `failed` and served forever, and nothing in M2 mints a new `attempt`, so the
run is bricked with no documented recovery.

**What Task 20's REVIEW FIX adds to this task (2026-08-20).** A config error now produces a FAILED
record rather than an exception escaping the coordinator - an unwired executor named by an
available policy row is the live case. That upholds the always-a-record invariant, and it moves a
cost here: fixing the CONFIG does not change the node's journal key, so a plain resume replays the
failed record instead of re-attempting. The operator's loop is "fix the wiring, resume, nothing
happens". This task owns that: `resume --from <node>` must re-attempt a node whose failure was a
config error, and the test for it should fix the wiring between the two runs so it proves the
re-attempt actually reaches the executor. Note the test that used to argue the other way
(`test_work_refuses_the_resolved_executor_when_it_is_not_wired`) was updated deliberately, with the
trade-off written into `work()`'s docstring - read it before changing the behaviour back.

**A cheaper prevention worth considering here or in M5:** an available policy row naming an
executor nobody wired is a STARTUP inconsistency, detectable when the coordinator is composed. A
validation at wiring time would refuse the run with one clear message instead of failing whichever
node happens to resolve to that row, halfway through. That is a different mechanism from the retry
path and does not replace it - a row can become unwired for reasons a startup check cannot foresee.

**What Task 20's probe adds to this task:** a budget stop reaches the executor's error path looking
like a transient executor error (`is_error: true`, `subtype: "error_during_execution"` - measured,
`workflow/design/probes/m3-interrupt.md`). Whatever this task retries, it must NOT retry a node
that was stopped for exceeding its token ceiling; Task 20 stamps that record `transient=False`, and
this task's retry rule must key on that rather than on the error type alone. A test here proves the
two features do not combine into a loop that funds itself.

**Files:** `src/agentdag/application/kernel/dispatch.py`, `src/agentdag/adapters/cli/commands/run.py`
(`resume --from <node>`), `tests/test_kernel_dispatch.py`, `tests/test_cli_run.py`

**Interfaces:** `resume --from <node>` (DBOS's `fork_workflow` shape, the one mechanism D2 said to
copy): the named node and everything downstream of it are re-attempted with `attempt + 1`; every
node upstream is served as usual. `attempt` is already in the key identity, so a new attempt is a
new key by construction - no journal surgery.

**STOP conditions:** if re-attempting would require rewriting or deleting a journal line, stop: the
journal is append-only and a design change is a decision, not an implementation detail.

- [x] **CLOSED 2026-08-22, but under a DIFFERENT VERB than the one specified above.** Recorded here
  2026-08-27. The need this task names is met; `resume --from <node>` is not what shipped, and the
  Files and Interfaces blocks above still describe it, so read them as the plan's proposal rather
  than as the code. Design: `RESEARCH/workflow/design/2026-08-22-retry-grant.md`. Decisions: `DECISIONS.md` items
  11 and 13.

  **Two mechanisms shipped in place of one flag.**
  (a) AUTOMATIC re-dispatch, `afcf9ca`, which landed as `Coordinator._retries`; `2615e45` then split
  that body out as `_auto_retries` so the operator half could sit beside it rather than widen it, and
  `_retries` is now just `self._auto_retries(...) or self._granted(...)`.
  `Coordinator._auto_retries` (`src/agentdag/application/kernel/context.py:1216`) gives a
  failed CODE node another attempt when the record carries a TRANSIENT error, capped by
  `policy.max_attempts` (2 in the shipped table, so one retry). It reads the RECORD, not the status:
  a red gate is `failed` with no `error` at all because it ran and reported a real answer, and a
  `KernelError` is stamped `transient=False` because the same inputs reproduce it, so neither
  retries. This is what settles the Task 20 hazard above - a node stopped at its token ceiling is
  stamped `transient=False` and is therefore not re-dispatched into spending it again. No new journal
  event: `attempt` is an identity field, so every try appends its own `started` and `result` under
  its own key.
  (b) The OPERATOR verb, `e63122e`, `2615e45`, `0804fe1`, `24da3ec`, documented in `d7f4164`:
  `agentdag run retry RUN_ID NODE_ID` (`src/agentdag/adapters/cli/commands/run.py:387`). It covers
  ANY failed record, and it is bound to the (node id, journal KEY) of the failed attempt, so the
  attempt it authorises runs under `attempt + 1` - a different key - and the grant can never match
  twice. Self-limiting by construction: no counter, no consumed flag, no way for an unattended run
  to loop on a grant nobody withdrew.

  **Why `resume --retry` / `resume --from` LOST** (`DECISIONS.md` item 13, and the reason to record
  this rather than quietly renaming the task): a red gate does not FAIL the run - graph A routes it
  into a tally row and the run reaches `done` - so the verb has to relaunch a DONE run, and
  `RESEARCH/workflow/design/mcp-surface.md` states two of `resume`'s own properties in terms of refusing exactly
  that. A flag would make both of them conditional; `approve.py` had already rejected that shape for
  deadlines ("two triggers with different semantics is two policies to reason about").

  The plan's other premise held: nothing here required rewriting or deleting a journal line, so the
  STOP condition never fired.

---

### Task 25: served dispatches - same-node only, and the collision as a drift signal

**Decided by the user 2026-08-20.** A stored record is served ONLY to the node it belongs to. A
different node id hitting the same key runs and gets its own record. No journal or schema change,
replay purity intact.

**Files:** `src/agentdag/application/kernel/dispatch.py` (the `served is not None` branch at
`dispatch.py:147-151`), `src/agentdag/application/kernel/summary.py` (a `key_collisions` drift
signal), `tests/test_kernel_dispatch.py`

**Interfaces:** in `dispatch`, serve only when `served.node_id == spec.node_id`; otherwise fall
through to `_run_and_record`. Count the fall-throughs and report them in the run summary line.

- [x] Steps: RED (two ids, identical work: both must get their own record, and the summary must
  report one collision), implement, mutation-check, gate, commit.

**SHIPPED 2026-08-27.** With one correction to the interface above, found by testing it. Checking
`served.node_id == spec.node_id` at the serve site while the index stays keyed by the key ALONE
breaks the replay purity this task claims to keep: two records under one key means `results[key]`
holds the LAST one, so on replay the FIRST node fails that check and re-runs work it had already
done. The index is therefore keyed by `(node_id, key)` - the shape `grants` in the same module
already uses, and for the same stated reason - and the serve site looks up that pair. Both forms
were mutation-checked: the pre-fix code fails two of the new tests, and the literal form above
fails `test_on_replay_each_twin_is_served_its_own_record`.

`key_collisions` lands on the run summary line and in `journal-line.schema.json` as an OPTIONAL
property, so a run_summary line written before the signal existed stays valid. A key with several
records from ONE node (a retry, a crash-window redispatch) is not reported. The behaviour matches
what `RESEARCH/workflow/design/2026-08-21-insertion-mechanism.md` already assumed of it, including the name.

---

### Task 26a: retire the kernel's own host-wide gate lock, and MEASURE what it was costing

**Why now (2026-08-20).** `MakeTestGate` takes a host-wide `FileLock` around every `make test`, and
`composition/kernel.py` hands the whole coordinator ONE `_GATE_LOCK`. So in a parallel map every
branch's gate queues behind every other branch's - a two-branch graph runs its gates strictly
serially, and every M2 measurement was taken under that constraint. The lock exists because bmk used
to rebuild its shared tool environment and re-sync a project venv with no guard of its own. bmk
3.17.0 now guards both, and more narrowly than we could: a SHARED lock on the tool env for each bmk
process's lifetime, EXCLUSIVE only around the upgrade, and a separate exclusive lock scoped to the
venv sync alone. Readers no longer exclude readers.

**SHIPPED IN PART, 2026-08-20, by another session.** `b9ee2de` on main already removed the lock and
the `_GATE_LOCK` wiring, for exactly the reason above, and added
`test_two_gates_run_concurrently_rather_than_serialising` - a real discriminator, since each gate
touches its own marker and then waits up to 10 s for the other's, so under serialisation the first
can never see the second and exits non-zero. Do NOT redo that work. What remains open is the
correctness half below: their two gate commands are trivial Python, so nothing in that test
exercises bmk, and the WRONG-PASS risk is still carried by a code read rather than a measurement.
Treat the boxes below as: the first three are superseded (the before/after timing was never taken
and the lock is already gone, so say that rather than inventing a retrospective number), the fourth
and fifth are the task.

**This is a measured change, not a deletion.** The order matters:

- [ ] FIRST measure what the lock costs: an attended graph A run at `parallel=2` with the lock in
  place, recording each gate node's start and end from the journal, so the serialisation is a number
  rather than an inference.
- [ ] Remove the lock (and the `_GATE_LOCK` wiring), keeping `MakeTestGate`'s injectable `lock`
  parameter only if something still needs it - if nothing does, remove it rather than leaving a
  disabled seam.
- [ ] Re-run the same graph and report both numbers. If the gain is inside the noise, say so and
  keep the removal anyway on the grounds that the mechanism is redundant - but do not claim a
  speed-up that was not measured.
- [ ] Prove correctness under concurrency, which is the half that actually matters: two branch gates
  running at once must both produce true verdicts. The failure this guards against is not a crash but
  a WRONG PASS, so a test that merely runs two gates concurrently and sees green proves nothing -
  it must show that a genuinely failing branch still fails while another gate runs beside it.
- [ ] `BMK_MIN` in the generated Makefile must be at least 3.17.0, since the removal depends on
  bmk's own locks existing. Say what happens if an older bmk is ever forced.

**STOP** and report if the branches turn out to share anything else the gate lock was incidentally
serialising - the whole risk of removing a lock nobody enumerated is what it was protecting BESIDES
its stated purpose.

---

### Task 26: the carried Minors, in one sweep

From review B's fix-in-M3 triage: `TierRow.billing` and `Escalation.*` as `StrEnum`; the stray
`.tmp-*` retention sweep in `decisions/`; alias-to-alias rebinding evading `workflow_check`;
`--policy`/`--runs` relative paths through `resolve_path`; `--parallel`/`--policy` persisted in
`state.json` so a relaunch does not silently fall back to config; the scanner-versus-live-executor
race (skip-on-vanish); the two approve refusal branches still untested; `launch.log` 0600 untested.

Each gets a test; none gets a redesign. If one turns out to need a design decision, STOP and report
it rather than deciding it inside a sweep.

- [ ] Steps: one commit per group (domain enums / store retention / CLI paths / tests), gate, commit.

---

### Task 27: the attended M3 run, the note, the PR

As Task 18 did for M2: real runs on scratch clones with the new mechanisms exercised - a cap hit, a
deadline, a cancel, a timer-applied default, a bricked node resumed with `--from` - each measured
and written into `workflow/design/probes/m3-kernel.md` in RESEARCH with the raw numbers. Then the
PR, CI by head sha AND workflow name, the final whole-branch review, one fix round, merge.

**The M2 lesson to apply BEFORE pushing:** run `pyright --pythonpath .venv/bin/python
--pythonversion 3.12` and again under `--pythonplatform Windows` and `Darwin`, and grep for
`from tests\.` - four of M2's five CI rounds were portability failures the local gate could not see.

---

## After M3 (M6 then M5, at mid-plan altitude; M4 is cut and its number is not reused)

PARKED, to be scheduled when the user says so: the `Sandbox` port's CONTAINER adapter. Task 19
defines the port and ships `none` only. The container is the boundary that makes an unattended run
safe to point at anything but our own repositories - it is the only one of the shapes considered
that closes NETWORK EGRESS, which is the hole the M2 review measured - and until it lands the
README's "what the kernel does not enforce" section is the honest statement of where we are. A VM
adapter follows it only if work is pointed at repositories we do not control; a separate-unix-user
adapter is decided against, not deferred (design 3.1 carries the reasoning).

**Design 3.8's CONTEXT CEILING was deferred out of M3 here and then BUILT OFF-PLAN, 2026-08-22 to
2026-08-24, before Task 27 ran.** The paragraph that stood here said it was deferred deliberately -
it attaches to the same `_on_turn` seam as Task 20's token cap, so it is cheapest to build once that
seam is real and measured, and it is a behaviour change to every long node rather than a mechanism
the other M3 work depends on - and it ended "Schedule it after Task 27's measurements say what a
node's first-turn and total input actually look like under the cap." That scheduling condition was
not met and is now void: Task 27 has not run. Corrected 2026-08-27, and left as a correction rather
than a rewrite because the deferral's REASONING is still the reasoning, and it is what the sequencing
was traded away against.

What landed: the handover nudge, `needs_continuation`, the successor dispatched with
`continuation + 1`, `max_continuations`, the grace after the notice, and the coordinator's identity
stamp - in order, `95297ca`, `65eb765`, `804ad36`, `044016a`, `ede998a`, `b449cae`, `949f87c`,
`8927277`, `4cb3c5e`. Decisions 14, 15 and 16 in `DECISIONS.md` cover the choices. Three probe
write-ups sit under `RESEARCH/workflow/design/probes/`: `handover-nudge-inject.md`, `handover-grace-expiry.md`, and
`live-handover.md`, which is the one live run against agentdag's own executor - a node complied 6 of
6, wrote a handover, the coordinator stamped it, and every record validated against the shipped
schema. Read that document's own qualification with it: 6 of 6 is 3 launches of ONE brief at a
LOWERED ceiling, so it settles that the path runs and settles nothing behavioural.

**The gap that shipped with it, recorded here because it is load-bearing for M6 and is not visible
from any of the above.** Nothing composes a predecessor's handover into its successor's brief. The
successor is a fresh dispatch of the SAME `brief` and `input_obj` - `Coordinator._dispatch`'s
continuation loop re-enters `_dispatch_once(spec, brief=brief, input_obj=input_obj, body=body)` with
only `continuation` and `attempt` changed (`context.py:1032-1050`). And `_stamp_handover` is
documented as "the coordinator's only read of `handover.json`" (`context.py:1106`); it writes back
identity keys and nothing else. So the record a node hands over is written, stamped, schema-valid,
and read by nobody: `b449cae` is titled "Say what reads the handover record, which is nothing yet".
A producer with no consumer is a shape this document set has caught before and named at source -
`spec.isolation` with zero behavioural readers (`RESEARCH/workflow/design/2026-08-21-dispatchability.md`) and
`brief_ref` with a validator rule and neither reader nor writer
(`RESEARCH/workflow/design/2026-08-22-dispatch-seam.md`) - and whatever consumes the handover is unbuilt work that
no task here owned until 2026-08-28: it is M6 component 7 in the mid plan now.

**The Codex arm is CUT** (`build-plan-high.md`, 2026-08-21). A second executor proves a
portability nobody asked for, and it took with it the UNRESEARCHED terms question for a ChatGPT
subscription driven by a coordinator, which is therefore no longer owed an answer. Its section
sits in `build-plan-mid.md` under `## Cut`, with the reason, rather than being deleted.

**M6 is the next milestone with tasks** (decided with the user 2026-08-21: M6 exists and is
ungated). M5 follows it as its demonstration.

**Four small items sit UNOWNED in the mid plan, and this is the note that stops them becoming a
fifth workstream by accident.** The 2026-09-01 OpenClaw 2.0 source read produced four small gaps -
a node-level lease with heartbeat and reclamation, a stuck-state taxonomy, a credential TTL bound
to (run, node, attempt), and re-authorizing under the writer barrier - recorded in the mid plan
under "Decided, not yet owned by any milestone". Two of them (the lease, and re-authorizing under
the barrier) attach to seams M3 already owns, so they are CANDIDATES for M3's tail if the user
wants them owned rather than unowned. No task is written for them here, deliberately: none is the
next decision checkpoint, and the detailed plan is written only that far.

**The per-repo selection rule that stood here is RETIRED.** It read: "Its chore must be one that
genuinely needs per-repo judgement, measured at about 4 of 29 big sweeps." That is the demand
argument the high plan removed on 2026-08-21 - the subject is ONE COMPLEX TASK broken down across
sub-agents, and the user was explicit that the repos case is not relevant. A builder following the
old rule would pick a fleet chore and pass the whole-build exit criterion with a run the governing
page says demonstrates nothing. M5's selection is the high plan's three clauses, applied to what
the PLANNER must be able to decompose.

M5 is still where the M1 baseline stops being the control and is retired - and it was never usable
as one anyway: `m1-baseline.md` names no artifact by path and its evidence was destroyed by M2's
own `git clone --mirror --refresh`.

**C1 and C2 still get no detailed tasks, and that is deliberate** - they are decision checkpoints
that ship no code. What CHANGED on 2026-08-21 is what they decide: they inform M6's SHAPE (node
granularity, whether briefs and a cost model earn their cost) and no longer gate its existence,
because they compare a structured graph against prose while M6's justification is durable
execution across a crash outside a session. Different axis.

**M6 is DESIGNED (2026-08-28): `RESEARCH/workflow/design/2026-08-28-planning-loop-design.md`, six user decisions.**
The paragraph that stood here said the first M6 task was to design dispatchability. That design
now exists - it is the op registry, decision 3 - and the three seam documents it replaces stay dead.
The tasks below are Checkpoint A of the mid plan's M6 section: the double-load probe, the schema,
the registry, and graph A expressed as a plan over that registry. Nothing past Checkpoint A is
planned in detail until Task 31 has answered, because its answer can grow the registry or stop the
build.

**M6 branch discipline.** Code tasks commit on `feat/kernel-m6` in a worktree at
`agentdag/.claude/worktrees/kernel-m6` (the layout M3 used), `make test` before every commit, no
push until the user says. Probe tasks commit in RESEARCH on `main`. Every task's requirements
include the Global Constraints at the top of this file.

---

### Task 28: M6 probe - does a worktree inside the repo double-load the repo's own CLAUDE.md?

**Why first.** Decision 6 puts a node's worktree under `<repo>/.claude/worktrees/<run>/` so the
walk-up finds the ancestor cascade. A git worktree is a checkout, so it carries the repo's tracked
`CLAUDE.md` itself, and the parent repo carries the same file one level up. Whether Claude Code
loads it once or twice is a harness behaviour, and the rule is to probe it before building on it.
Self-report is not evidence; the captured request body is.

**Files:**
- Create: `workflow/probes/probe_cascade_worktree.py` (RESEARCH)
- Create: `workflow/probes/probe_cascade_worktree.result.json`
- Create: `workflow/design/probes/cascade-worktree.md`
- Reuse, unchanged: `workflow/probes/probe_prompt_drift_proxy.py`

**Interfaces:**
- Consumes: the proxy's capture directory of `req-NNN.json` bodies; `claude_agent_sdk`
  `ClaudeAgentOptions(cwd=..., setting_sources=["project"], system_prompt=<plain str>)`.
- Produces: a note stating the marker count per arm, and the placement consequence for decision 6.

**Out of scope** - do NOT touch, though they look related:
- `agentdag/src/agentdag/application/kernel/context.py:262` (the cwd invariant) - this task
  measures; component 8 changes it afterwards.
- `setting_sources=None` arms - the user-level plugin set is not the question here.

**STOP conditions** - stop and report rather than improvise, if:
- the proxy does not capture a body for the control arm (the instrument is broken, not the cascade);
- the marker appears ZERO times in the control arm (then `setting_sources=["project"]` did not load
  the project file and every other arm is uninterpretable);
- a dispatch returns `is_error: true` (record it; do not read a count off an errored body).

- [x] **Step 1: Build the three trees, each with a unique marker**

Under a scratch root OUTSIDE every git work tree (the run-dir rule), create:

```
A/repo/                      git init; CLAUDE.md containing "CASCADE-MARK-<nonce>"; commit
B/repo/                      same; then: git worktree add .claude/worktrees/wt1 -b probe
C/repo/  and  C/wt-outside/  same repo; git worktree add ../wt-outside -b probe
```

Arm A dispatches with `cwd=A/repo` (control: expect exactly 1). Arm B dispatches with
`cwd=B/repo/.claude/worktrees/wt1` (the question: 1 or 2). Arm C dispatches with
`cwd=C/wt-outside` (expect 1: the checkout carries the file, no ancestor does).

- [x] **Step 2: Drive the three arms through the proxy**

```python
# probe_cascade_worktree.py, the drive half - the proxy runs separately on <port>
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
for arm, cwd in (("A_root", a_root), ("B_wt_inside", b_wt), ("C_wt_outside", c_wt)):
    opts = ClaudeAgentOptions(cwd=str(cwd), setting_sources=["project"],
                              system_prompt=f"probe {nonce}. Reply OK. No tools.", max_turns=1)
    ... dispatch, record ResultMessage.usage and is_error per arm ...
```

Run: `uv run workflow/probes/probe_prompt_drift_proxy.py <capture> <port>` then
`uv run workflow/probes/probe_cascade_worktree.py <capture> <nonce> <port>`

- [x] **Step 3: Count the marker in each captured body, by code**

```python
for body in sorted(capture.glob("req-*.json")):
    n = json.dumps(json.load(open(body))).count(f"CASCADE-MARK-{nonce}")
```

Expected: A = 1, C = 1. B is the result. Write all three plus `input_total` per arm to the
result JSON.

- [x] **Step 4: Write the note, and say what decision 6 must do**

`cascade-worktree.md`: date, CLI and SDK versions, the command, the counts per arm, MEASURED tier
on every number. Then ONE paragraph: if B = 1, decision 6 stands as written; if B = 2, record the
duplicated bytes and the two ways out (accept the cost; or place the run's worktrees one level
above the repo so the walk-up finds the ancestors but not the repo's own file - which LOSES the
repo's untracked `CLAUDE.local.md`, so it is a trade the user decides, not this probe).

- [x] **Step 5: Commit in RESEARCH**

Message via `-F` file. Raw bodies stay in the scratch capture dir; only the result JSON and the
note are committed.

**Result, MEASURED 2026-08-28 (`workflow/design/probes/cascade-worktree.md`, nonce `wt2828d`):
B = 1.** The repo's `CLAUDE.md` loads once from a worktree inside it, from the worktree's own copy;
the parent checkout's copy is not read even when it differs (also from a worktree at a plain
nested path). Decision 6 stands on the double-load question. The probe grew to eleven arms because
the first run's answer rested on an unmeasured premise, and that measured two more things:

- ancestors ABOVE the git root DO load under the SDK, from a repo root and from a worktree inside
  it - decision 6's premise holds;
- `CLAUDE.local.md` loads ONLY with `local` in `setting_sources`. The design's `["user", "project"]`
  gave a node the `CLAUDE.md` cascade and NONE of the memory index (which lives in
  `CLAUDE.local.md` at every level). **DECIDED 2026-08-28 (user): add `local`.** Design doc
  section 9 now reads `["user", "project", "local"]`. The side effect is measured too (arms L, M):
  from a worktree node both the parent checkout's `.claude/settings.local.json` and the worktree's
  own load, so component 8 must treat the operator's project-local permissions and hooks as part
  of what a node inherits.
- **The shipped triple was then run AS ONE arm (N), with two ancestor levels and both settings
  files.** All six files load, both hooks fire. Its control (O, the same tree without `user`) loads
  the SAME six files, so `user` adds nothing to the project walk-up; what it demonstrably adds is
  tools - 171 definitions against 25, and 109,752 input tokens against 42,276 on a trivial
  dispatch. That 67,476-token premium is the operator's plugin and MCP set. It does NOT license
  dropping `user` (the user's correction, 2026-08-28): arm O also lost the whole skill catalogue
  and the hooks, since plugins are enabled in user settings. The composition is the finding: 142
  of the 171 are MCP tools from browser/IDE/desktop plugins (157k of 269k tool chars); in
  messages the skill listing adds ~3.5k chars and hook OUTPUT 17.4k (SessionStart injection plus
  per-prompt recall, both already on the node exclusion list). **Component 8 owns the lever:** a node runs under `node_dir/home/.claude`
  (`executor_claude.py:269`), so its `user` layer is whatever that home holds - bitranox in, the
  MCP-heavy plugins out. Owed there: how a node home gets the bitranox plugin without the
  operator's `~/.claude` (the plugin cache and `installed_plugins.json` are per-home), and one
  probe arm with such a home to measure the real figure. Full note in `build-plan-high.md` M6
  risk 3.

---

### Task 29: domain - Plan, Entry, Condition, and the condition evaluator

**Files:**
- Create: `src/agentdag/domain/condition.py`
- Create: `src/agentdag/domain/plan.py`
- Create: `src/agentdag/schemas/plan.schema.json`
- Test: `tests/test_domain_condition.py`, `tests/test_domain_plan.py`
- Modify: `docs/systemdesign/module_reference.md` (two rows; the list is checked against disk)

**Interfaces:**
- Consumes: `NodeSpec` from `domain/models.py`, UNCHANGED; `ResultLine.key_facts` from
  `domain/journal.py`.
- Produces (later tasks rely on these exact names):

```python
# domain/condition.py - pure, no I/O
class FieldRef(BaseModel):  # frozen, extra="forbid"
    entry: str  # an entry's node id
    field: str  # a key_facts field name


class Compare(BaseModel):  # {"ref": FieldRef, "op": "<=", "value": 20}
    ref: FieldRef
    op: Literal["==", "!=", "<", "<=", ">", ">="]
    value: int | float | str | bool


class AllOf(BaseModel):
    all: tuple["Condition", ...]


class AnyOf(BaseModel):
    any: tuple["Condition", ...]


class Not(BaseModel):
    not_: "Condition"  # serialised as "not"


Condition = Compare | AllOf | AnyOf | Not


def evaluate(cond: Condition, records: Mapping[str, Mapping[str, object]]) -> bool | None:
    """True/False, or None when ANY referenced field is absent - absent is reported, never counted."""


def referenced_fields(cond: Condition) -> frozenset[FieldRef]: ...


# domain/plan.py
class Entry(BaseModel):  # frozen, extra="forbid"
    spec: NodeSpec
    op: str  # a registry name, or "plan"
    args: Mapping[str, object]
    brief: str
    output_contract: frozenset[str]
    acceptance: Condition | None


class Plan(BaseModel):
    goal: str
    entries: tuple[Entry, ...]
    holds_while: Condition | None  # absent = vacuously true; decided, and tested
    done_when: Condition  # REQUIRED
    deps: tuple[str, ...]
```

**Out of scope** - do NOT touch, though they look related:
- `domain/models.py` `NodeSpec` - Task 25's test pins its exact field set; a plan sits ABOVE it.
- `domain/validate.py` (`validate_spec`) and `application/kernel/dispatchable.py` - retired by a
  later task, not edited here.
- The registry - Task 30. This task knows nothing about which ops exist.

**STOP conditions** - stop and report rather than improvise, if:
- `NodeSpec` no longer matches the field set `tests/test_kernel_executor_port.py` asserts;
- pyright strict wants a suppression to type the recursive `Condition` union (define the types;
  a `RootModel` or a forward ref, never `# type: ignore`);
- a step's verification fails twice after one reasonable fix attempt.

- [ ] **Step 1: RED - the condition evaluator**

```python
def test_compare_evaluates_against_key_facts():
    cond = Compare(ref=FieldRef(entry="w_scan", field="repo_count"), op="<=", value=20)
    assert evaluate(cond, {"w_scan": {"repo_count": 47}}) is False
    assert evaluate(cond, {"w_scan": {"repo_count": 12}}) is True


def test_absent_field_is_none_not_false():
    cond = Compare(ref=FieldRef(entry="w_scan", field="repo_count"), op="<=", value=20)
    assert evaluate(cond, {}) is None  # entry missing
    assert evaluate(cond, {"w_scan": {}}) is None  # field missing


def test_all_any_not_compose_and_none_propagates():
    a = Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0)
    b = Compare(ref=FieldRef(entry="w", field="count"), op=">", value=0)
    recs = {"g": {"rc": 0}}  # b's field absent
    assert evaluate(AllOf(all=(a, b)), recs) is None  # cannot say True
    assert evaluate(AnyOf(any=(a, b)), recs) is True  # a alone decides
    assert evaluate(Not(not_=a), recs) is False


def test_referenced_fields_lists_every_ref():
    cond = AllOf(
        all=(
            Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0),
            Not(not_=Compare(ref=FieldRef(entry="w", field="n"), op="<", value=1)),
        )
    )
    assert referenced_fields(cond) == frozenset({FieldRef(entry="g", field="rc"), FieldRef(entry="w", field="n")})


def test_condition_refuses_free_text_and_unknown_op():
    with pytest.raises(ValidationError):
        Compare.model_validate({"ref": {"entry": "g", "field": "rc"}, "op": "matches", "value": "x"})
    with pytest.raises(ValidationError):
        Compare.model_validate(
            {"ref": {"entry": "g", "field": "rc"}, "op": "==", "value": 0, "note": "and the tests pass"}
        )
```

- [ ] **Step 2: Run to verify RED**

Run: `.venv/bin/python -m pytest tests/test_domain_condition.py -v`
Expected: FAIL, `ModuleNotFoundError: agentdag.domain.condition`

- [ ] **Step 3: Implement `domain/condition.py`**

Pure functions over Pydantic models; `evaluate` returns `None` the moment any leaf's field is
absent, EXCEPT that `AnyOf` short-circuits to `True` on the first `True` leaf (a decided leaf
outranks an undecided one only in the direction that cannot be wrong). Google-style docstrings
with the absent-case rule stated in `evaluate`'s.

- [ ] **Step 4: GREEN, then RED for the plan models**

```python
def test_plan_round_trips_json_and_requires_done_when():
    data = {
        "goal": "migrate the fleet",
        "deps": [],
        "entries": [
            {
                "spec": SPEC_JSON,
                "op": "work",
                "args": {},
                "brief": "do it",
                "output_contract": ["status"],
                "acceptance": None,
            }
        ],
        "holds_while": None,
        "done_when": {"ref": {"entry": "n0", "field": "status"}, "op": "==", "value": "passed"},
    }
    plan = Plan.model_validate(data)
    assert Plan.model_validate_json(plan.model_dump_json()) == plan
    with pytest.raises(ValidationError):
        Plan.model_validate({**data, "done_when": None})


def test_absent_holds_while_is_vacuously_true():
    plan = Plan.model_validate(MINIMAL)  # holds_while omitted
    assert plan.holds_while is None
    assert evaluate_holds_while(plan, {}) is True  # the decided absent case, pinned
```

- [ ] **Step 5: Implement `domain/plan.py` and `schemas/plan.schema.json`**

Generate the JSON schema from the models (`Plan.model_json_schema()`), commit it as a file, and add
a test that the committed file equals the generated one - the schema is a shipped artefact, so
drift between it and the model is a test failure, not a surprise.

- [ ] **Step 6: Gate and commit**

Run: `make test` (read its RC from the captured log). Commit on `feat/kernel-m6` via `-F` file:
"Add Plan, Entry and Condition: what a planner emits, and how a condition is decided".

---

**RENAMED 2026-08-28, after Task 29's review (USER decision).** This block originally named the
classes `All` and `Any_`. `Any_` fired ruff N801, and the implementer added a per-file-ignore for
it; the standing project rule is to SOLVE a fired lint rule rather than exempt it, so the classes
are `AllOf` and `AnyOf` and the ignore is deleted. Verified: `class AnyOf(BaseModel)` passes
`ruff --isolated --select N801` clean. The WIRE FIELD NAMES are unchanged - still `all`, `any`,
`not` - so only the class names and the schema's `$defs` keys move. Tasks 30 and 31 consume the
NEW names.

### Task 30: the op registry, and plan validation by absence

**Files:**
- Create: `src/agentdag/application/kernel/registry.py`
- Create: `src/agentdag/application/kernel/plan_validate.py`
- Modify: `src/agentdag/composition/kernel.py` (register the ops that have bodies today)
- Modify: `src/agentdag/domain/policy.py` `RunLimits` (+ `max_replans: int`,
  `max_nodes_per_run: int`, `max_nodes_per_plan: int`); `src/agentdag/policy/tier-policy.yaml`
  `run_limits` (values: 3, 200, 40 - the E1 distribution's top is 38 nodes)
- Test: `tests/test_kernel_registry.py`, `tests/test_kernel_plan_validate.py`,
  `tests/test_policy.py` (the three new bounds parse)

**Interfaces:**
- Consumes: `Plan`, `Entry`, `Condition`, `referenced_fields` from Task 29; `RunLimits`;
  `Coordinator._dispatch(spec, *, brief, input_obj, body)` as the one seam (`context.py:889`).
- Produces:

```python
# application/kernel/registry.py
class UnregisteredOp(KernelError): ...

@dataclass(frozen=True, slots=True)
class OpSpec:
    name: str                          # "work", "gate:make-test", "scan", "reduce:count", "approve", "plan", "judge"
    args_model: type[BaseModel]        # validates Entry.args; extra="forbid"
    output_contract: frozenset[str]    # key_facts fields the body emits
    can_change_state: bool             # False for every gate:* (regression only); decision 4
    build: Callable[[Entry, "PlanContext"], Body]   # what _dispatch takes

class OpRegistry:
    def register(self, op: OpSpec) -> None       # duplicate name raises KernelError
    def get(self, name: str) -> OpSpec           # absent raises UnregisteredOp
    def names(self) -> frozenset[str]

# application/kernel/plan_validate.py
@dataclass(frozen=True, slots=True)
class Accepted:
    plan: Plan                                   # node ids ALLOCATED by the coordinator
@dataclass(frozen=True, slots=True)
class Refused:
    reasons: tuple[str, ...]                     # every reason, not the first

def validate_plan(plan: Plan, *, registry: OpRegistry, graph: Mapping[str, NodeSpec],
                  limits: RunLimits, is_root: bool, allocate_id: Callable[[], str]) -> Accepted | Refused:
    """Whole or nothing (decision 1). Root rule per decision 4."""
```

**Out of scope** - do NOT touch, though they look related:
- `application/kernel/dispatchable.py`, `domain/validate.py` - retired in a later task once the
  registry is on the dispatch path; deleting them now orphans their tests for no gain.
- The `plan` and `judge` bodies - registered here with a body that raises `KernelError("not yet
  wired: M6 component 3")`, so `validate_plan` can accept a plan that names them and Task 31 can
  ask the registry what graph A needs. That is the ONE placeholder this plan carries, and it is
  named so it cannot pass as a mechanism.
- `Coordinator` itself - no dispatch path changes here.

**STOP conditions** - stop and report rather than improvise, if:
- `context.py:889 _dispatch` no longer has the `body` parameter this seam rests on;
- the composition root's shape makes registering a body constructor need a Coordinator instance
  at import time (then the registry needs a factory, and that is a design note, not a workaround);
- a step's verification fails twice after one reasonable fix attempt.

- [ ] **Step 1: RED - the registry**

```python
def test_registry_refuses_duplicate_and_absent():
    reg = OpRegistry()
    reg.register(WORK)
    with pytest.raises(KernelError):
        reg.register(WORK)
    with pytest.raises(UnregisteredOp):
        reg.get("apply")  # never registered, by decision
    assert "apply" not in reg.names()
```

- [ ] **Step 2: RED - validation, one test per rule**

```python
def test_unregistered_op_is_refused_whole():
    plan = plan_with(entries=[entry(op="work"), entry(op="teleport")])
    out = validate_plan(plan, registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids())
    assert isinstance(out, Refused) and any("teleport" in r for r in out.reasons)


def test_args_are_validated_by_the_ops_model():
    out = validate_plan(plan_with(entries=[entry(op="gate:make-test", args={"argv": 5})]), ...)
    assert isinstance(out, Refused)


def test_condition_may_reference_only_declared_contract_fields():
    e = entry(op="work")  # contract: {"status", "artifact_ref"}
    bad = Compare(ref=FieldRef(entry="n0", field="repo_count"), op="<=", value=20)
    out = validate_plan(plan_with(entries=[e], holds_while=bad), ...)
    assert isinstance(out, Refused)


def test_deps_may_name_only_graph_or_earlier_entries(): ...


def test_node_ids_are_allocated_never_taken_from_the_model():
    plan = plan_with(entries=[entry(op="work", node_id="evil/../key")])
    out = validate_plan(plan, ..., allocate_id=lambda: "n-0001")
    assert isinstance(out, Accepted) and out.plan.entries[0].spec.node_id == "n-0001"


def test_more_than_max_nodes_per_plan_is_refused(): ...


def test_root_done_when_over_only_gate_fields_is_refused_unless_judged():  # decision 4
    gate_only = Compare(ref=FieldRef(entry="n0", field="rc"), op="==", value=0)
    out = validate_plan(plan_with(entries=[entry(op="gate:make-test")], done_when=gate_only), ..., is_root=True)
    assert isinstance(out, Refused) and any("cannot change state" in r for r in out.reasons)
    with_judge = plan_with(
        entries=[entry(op="gate:make-test"), entry(op="judge")],
        done_when=AllOf(all=(gate_only, Compare(ref=FieldRef(entry="n1", field="verdict"), op="==", value="pass"))),
    )
    assert isinstance(validate_plan(with_judge, ..., is_root=True), Accepted)


def test_same_plan_not_root_is_accepted():  # the rule is a ROOT rule
    ...


def test_plan_op_is_registered_and_apply_is_not():
    assert "plan" in REG.names() and "apply" not in REG.names()
```

- [ ] **Step 3: Implement registry, validator, the three bounds, and register the ops**

Composition root registers: `work` (`Coordinator.work`), `gate:make-test` (`Coordinator.gate` with
the make-test argv), `scan` (`Coordinator.scan`), `reduce:count` (a fold that counts records with
`status == passed`), `approve` (`Coordinator.approve`), `plan` and `judge` (the named
not-yet-wired bodies). Every `gate:*` is `can_change_state=False`; everything else `True`.

- [ ] **Step 4: GREEN, mutation-check decision 4**

Flip `can_change_state` on `gate:make-test` to `True` in a scratch copy: the decision-4 test must
FAIL. Restore from the copy, never from git (the uncommitted-work rule).

- [ ] **Step 5: Gate and commit**

`make test`; commit on `feat/kernel-m6`: "Add the op registry: a plan entry names an op the
composition root registered, or is refused".

---

### Task 31: M6 probe - graph A as a plan over the registry, no dispatch

**Why.** This is the design's first contact with a real graph. `RESEARCH/workflow/design/graphs/A-fleet-migration.md`
and `application/workflows/graph_a.py` are the one workflow that runs; if it cannot be written as a
`Plan` that `validate_plan` accepts, either an op is missing (the registry grows) or a CONSTRUCT is
missing (the design is wrong). Either answer is the checkpoint's output.

**Files:**
- Create: `workflow/probes/probe_graph_a_as_plan.py` (RESEARCH; imports agentdag from its venv)
- Create: `workflow/probes/probe_graph_a_as_plan.result.json`
- Create: `workflow/design/probes/graph-a-as-plan.md`

**Interfaces:**
- Consumes: `Plan` (Task 29), `validate_plan` and the composition root's registry (Task 30),
  graph A's node table.
- Produces: the accepted/refused verdict with every reason; the list of ops graph A needs that
  the registry lacks; the list of Python branches in `graph_a.py:program()` with no plan
  equivalent (each named by line).

**Out of scope:** dispatching anything. Editing `graph_a.py`. Adding ops - that is the decision
the note asks for.

**STOP conditions:** the registry import fails (Task 30 not merged to the worktree this runs
against); `validate_plan`'s signature differs from Task 30's.

- [x] **Step 1: Transcribe graph A's node table into a Plan JSON, by hand, one entry per row**

Every `w_migrate@i` is an entry with `op="work"`; `g_test@i` is `op="gate:make-test"`; the
isolation scan is `op="scan"`; `s_push_intent` is `op="stage"` IF registered (it is not, in Task
30 - record that); `ap_push` is `op="apply"` (never registered - record that it is REFUSED, which
is decision 8 holding); `r_dedup`-style folds are `op="reduce:count"`. `done_when`: every
`w_migrate@i.status == passed` and every gate rc 0, plus the approve decision.

- [x] **Step 2: Validate, and collect every reason**

```python
out = validate_plan(
    plan, registry=build_production().registry, graph={}, limits=limits, is_root=True, allocate_id=counter()
)
result = {
    "verdict": type(out).__name__,
    "reasons": list(getattr(out, "reasons", ())),
    "ops_needed": sorted({e.op for e in plan.entries}),
    "ops_registered": sorted(registry.names()),
}
```

- [x] **Step 3: Walk `graph_a.py:program()` and list every branch with no plan form**

By hand, line by line: each `if` over a record, each `map(...)` with a Python `body`, each literal
`argv`. For each: "expressible as <op> with args <...>" or "NOT expressible - needs <construct>".

- [x] **Step 4: Write the note, with the decision it asks for**

`graph-a-as-plan.md`: the verdict, the reasons, the two lists, provenance per line (READ the
table / RAN the validator / INFERRED the mapping). End with exactly one of: "the registry needs
ops X, Y - grow it" or "graph A needs construct Z that no op can express - the design is wrong at
<section>". That sentence is Checkpoint A's output and the user decides on it.

- [x] **Step 5: Commit in RESEARCH**

**Result, RAN 2026-08-28, CORRECTED 2026-08-29 after review
(`workflow/design/probes/graph-a-as-plan.md`, RESEARCH `e373198` then `3b79e9e`, against agentdag
`e086e73` in the unmerged kernel-m6 worktree): the registry needs ops - GROW IT. The design is not
wrong.** Nine arms, each with a predicted verdict; all nine held and all 16 line anchors matched.
`registered_subset_n2` (9 entries) is ACCEPTED, which licenses reading the refusals as findings
about graph A rather than about a validator that refuses everything.

The first version of this note said the opposite - that a plan entry cannot say WHERE it runs, so
the design was wrong at the `PlanContext` seam. An adversarial review refuted it with a
counter-example, now a permanent arm, and reading decision 6 of the planning-loop design (titled
"A node's cwd sits under the project tree") refuted it a second way. Both are recorded in the note
rather than deleted.

1. **Ops to add.** Exactly two of graph A's node ops are unregistered: `stage`, a real gap
   (`Coordinator.stage` exists, `planner_kinds` allows it, no OpSpec registers it), and `apply`,
   which is DECISIONS item 8 holding, correctly. The rest are CONTRACT gaps, each isolated by its
   own arm: `reduce:count` emits `{count}` while graph A's two folds emit `items[]`/`n` and the
   three tally counts, so a `done_when` naming `g_discover.n` or `r_tally.passed_count` is refused.
   Nothing emits a head sha, nothing clones, no registered op takes a working directory, and
   `scan`'s window starts at dispatch rather than before its watched node.
2. **Where a node runs is registry growth.** `Entry.args` is an opaque per-entry mapping, so an op
   whose args model carries a worktree needs no change to `Plan`, `Entry`, `NodeSpec` or
   `Condition`: `per_entry_worktree_n20` registers one such HYPOTHETICAL op on top of the real
   registry and validates 21 entries, twenty naming a different worktree, ACCEPTED. `PlanContext`
   is constructed nowhere in `src/` and `build()` takes it per call, so whether it is per-plan or
   per-entry is a component 3 decision, not a decided-wrongly one.
3. **NOT design faults:** the map fan-out, both routes and the empty-fleet halt are the nested
   `op="plan"` entry of decision 1 - registered, body raises, component 3 unbuilt. The size rule
   agrees rather than adding a problem: the registered subset validates to N=12 (39 entries) and is
   refused at 13, so unrolling N=20 into one plan is refused and the nested planner is what
   `max_nodes_per_plan` already wants.
4. **One real defect, separate and smaller than the headline it first got:** `NodeSpec.isolation`
   is parsed but never enforced (`enforced.py` exits 1; it is journal-key identity only), so
   decision 6's placement rule has no enforcement and the declarative form of "run this entry here"
   does nothing. Own worktree.
5. **Reframed, not a hole:** `validate_plan` accepts an apply-KIND spec under the registered op
   name `work`, but `spec.kind` grants no dispatch capability - the OP decides what runs - and
   section 10 of the planning-loop design schedules `planner_kinds` for retirement, "replaced by
   refusal-by-absence". What it leaves is `spec.kind` being decorative inside a plan, which that
   section is already scheduling away.
6. **New, small:** an approve node's declared `deadline_s` of 86400 against a 5400 s ceiling is
   neither refused, clamped, nor reported - the only clamp is inside `Coordinator.work`, which no
   code-kind node goes through.

Two errors in this task's own brief, both confirmed by running: `done_when`'s
`w_migrate@i.status == passed` is false for every record the coordinator can produce (`passed` is
graph A's tally ROW LABEL; the value is `"done"`), and Step 2's `build_production().registry` is
`build_op_registry()`, which takes no coordinator.


---

## Checkpoint A

Tasks 28 to 31 done. Read `graph-a-as-plan.md`'s last sentence with the user. Then, and only then,
write the detailed tasks for M6 components 3 and 4 (the planner op and the recursive loop, the
trigger-stop-barrier-redispatch path) up to Checkpoint B.

Task 28 is done (2026-08-28) and its result block above records what component 8's tasks must
carry when they are written, so the list is not lost between checkpoints: the source triple
`["user", "project", "local"]` (closed, user 2026-08-28); the curated node home (bitranox plugin
in, browser/IDE/desktop MCP plugins out) with its two owed items - how a node home obtains the
plugin without the operator's `~/.claude`, and one probe arm on such a home for the measured
figure; the lifecycle split as the place where SessionStart hook output is removed (of the 17.4k
chars, SessionStart carries ~8,976 via `session-banner.py` and UserPromptSubmit ~7,655 - earlier
text misnamed the first as the skill-router injection); and, recommended but not decided, a primer
dispatch per model row before that row's first parallel fan-out. None of these is a Checkpoint A
task.

**Component 8 gains three items from the 2026-08-28 decision that the UserPromptSubmit class stays
for nodes** (user: we want the learning signals; and requirement 2 item 3 already grants "memory
reads yes", which recall is):

1. **Set `REMEMBER_PROMPT_STAMP=stable` as an EXPLICIT POSITIVE VALUE in the node's env**, in
   `CredentialSource.child_env` (`executor_claude.py:178`) alongside `HOME`, or folded into
   `ClaudeAgentOptions.env` beside it. Three things make this narrower than "export it":

   - **Not a key in the node home's `config.json`.** `user-prompt-hook.sh` reads
     `${REMEMBER_PROMPT_STAMP:-full}` from the ENVIRONMENT, populated via `log.sh` and the env
     cache, so a config-only answer **fails open to `full`** on exactly the case that matters, a
     fresh node home's first prompt.
   - **Not an `_ALLOWLIST_KEYS` entry either.** `_allowlisted_env` only FORWARDS the coordinator's
     own value, and the coordinator does not set this one, so allowlisting still lands on `full`.
   - **And note the child already gets `full` today, by two paths that look opposite.**
     `_blank_everything_else` writes `""` for every inherited name the dispatch did not decide to
     carry, and `${VAR:-default}` substitutes on unset *or empty*. So a blanked value and an absent
     one both read as `full`. This is the shape [[feedback-a-check-can-be-well-formed-and-still-
     assert-nothing]] warns about: a test asserting "the stamp variable is not leaking through"
     passes today and would keep passing after this fix regressed. Assert on the value `stable`.

   **`stable`, not `off` (user, 2026-08-28).** Both are byte-stable, so `off` buys only the ~10
   chars of `[username]`. What `stable` keeps for that is the threshold-gated >=95% context
   warning, which changes bytes only when it changes behaviour - so it costs the cache nothing and
   is the one signal that a node is approaching its context limit. That matters MORE unattended,
   not less, because nobody is watching the context bar. I had recommended `off`; the reasoning
   that reversed it is the plugin's own note that the retained warning is exactly why both values
   exist.
2. **Confirm the export reaches the hook**, on the curated-home probe arm already owed. Hook
   environment inheritance is harness behaviour; it is one assertion on an arm that is being run
   anyway, so it costs no extra dispatch. A green arm here is what makes item 1 load-bearing.
3. **Measure the ordering question on the same arm.** Recall varies per brief, so a varying block
   stays in the tail by decision. Report whether the deterministic remainder (skills listing, agent
   types, MCP instructions, ~14k chars) sits behind it and is therefore also uncacheable. That
   answer, not the stamp, is now what settles requirement 5.

4. **If item 3 comes back bad, the fix need not be on agentdag's side.** Standing option, user-gated
   (user, 2026-08-28): where agentdag needs it, the bitranox self-learning skills and hooks can
   themselves be changed - they are ours. Giving `recall-memory.py` a bounded or task-keyed node
   mode turns the varying block into a small or stable one, which is the difference between
   requirement 5 passing and failing without agentdag giving up the read path. Two limits, both
   from the user: the trigger is agentdag NEEDING it (not a stricter last-resort bar), not a
   general improvement to the plugin, and each change is asked for before it is made. The mid plan
   lists the other three places this widens the option space, the load-bearing one being that
   `self-improve-gate.py` is the whole reason Stop cannot load for a node - a node-aware no-op
   there would make per-node capture possible rather than impossible.

---

### Task 32: the planner op - dispatch a planner node, parse and validate its Plan

**Files:**
- Create: `src/agentdag/application/kernel/planner.py`
- Modify: `src/agentdag/composition/kernel.py:398` - DELETE `_build_not_yet_wired`, give `plan` a
  real `_build_planner`, and REMOVE the `judge` registration entirely (Checkpoint B)
- Modify: `tests/test_kernel_registry.py` - Task 30 asserted `judge` was registered; that
  assertion inverts here and is the one existing test this task must change
- Modify: `src/agentdag/domain/plan.py` (add `PLAN_FILENAME`, beside the `handover.json` precedent)
- Modify: `src/agentdag/application/kernel/context.py` - ADD a `plan_node` primitive (see the
  defect note below); this file was Consumes-only when the task was written
- Modify: `tests/test_kernel_plan_validate.py` - three decision-4 tests used `judge` as a
  convenient True-flagged entry and must move to `work` when `judge` is unregistered
- Test: `tests/test_kernel_planner.py`

**DEFECT found on starting the task, 2026-08-29, and how it was settled.** As written this task
assumed a planner node could write `plan.json` and `dispatch_planner` could read it back through
`Coordinator.work`. It cannot, confirmed three ways: `work()` returns the executor's outcome and
`node_dir` never escapes its `body`; the executor sets `artefact_refs=[cwd_rel]`, the node's
WORKTREE, never a node-dir file; and `journal_key` needs `brief_hash` and `prefix`, neither of
which is on a `ResultRecord`, so the node dir is not derivable from a record either.

DECIDED (user, 2026-08-29): add a `plan_node` primitive to `Coordinator`, beside `work`/`gate`/
`scan`. Its body reads `plan.json` out of `node_dir` and puts the relative path in
`artefact_refs`, exactly as `gate()` already does with `gate.log`; `dispatch_planner` then
resolves it through `run_dir.read_text`, the way `_stamp_handover` reads `handover.json`. It is
the established pattern for every code primitive in this class. Rejected: writing the plan into
the node's CWD (it is a worktree under the project tree, so the plan lands in the repo and the
isolation scan sees a stray write); and returning the plan in `key_facts` (that field is per-node
typed facts a `Condition` references and every journal line carries, so a whole plan there bloats
replay, and it loses the on-disk artifact a human reads when a run goes wrong).

**Interfaces:**
- Consumes: `Plan`, `Entry`, `plan_json_schema()` (`domain/plan.py`); `validate_plan`, `Accepted`,
  `Refused` (`application/kernel/plan_validate.py`); `OpRegistry`, `PlanContext`, `Body`
  (`application/kernel/registry.py`); `Coordinator.work(spec, *, brief, cwd, prompt)`
  (`context.py:198`); `RunLimits` (`domain/policy.py`).
- Produces:

```python
# application/kernel/planner.py
PLANNER_PROMPT: str
"""The planner's system prompt. Carries plan_json_schema() and the registry's names(),
so the node is told what ops exist rather than guessing and being refused."""


@dataclass(frozen=True, slots=True)
class Planned:
    plan: Plan  # already through validate_plan, ids ALLOCATED
    record: ResultRecord  # the planner node's own record, for the journal


@dataclass(frozen=True, slots=True)
class NotPlanned:
    reasons: tuple[str, ...]  # parse failure, or validate_plan's reasons, verbatim
    record: ResultRecord  # still a record: the parent branches on it


async def dispatch_planner(
    *,
    spec: NodeSpec,
    goal: str,
    evidence: Mapping[str, ResultRecord],
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    is_root: bool,
    allocate_id: Callable[[], str],
) -> Planned | NotPlanned:
    """Dispatch one planner node and turn what it wrote into a validated Plan."""
```

**Out of scope** - do NOT touch, though they look related:
- The `judge` BODY - component 5's completion ladder owns it. This task does not build one.
- `execute` / the scheduler loop - Task 33. This task returns a `Planned`; nothing runs it yet.
- `plan_validate.py` - its rules are Task 30's and are correct as they stand. This task CALLS it.
- `domain/handover.py` - the successor-reads-handover gap is component 7, not this.

**STOP conditions** - stop and report rather than improvise, if:
- `Coordinator._dispatch(spec, *, brief, input_obj, body)` no longer hands its `body` the
  `node_dir` (`context.py:989`), which is the entire seam `plan_node` rests on;
- `gate()`'s `rel_log` pattern (`context.py:490`) is no longer how a code primitive surfaces a
  node-dir file, so `plan_node` would be inventing a convention rather than following one;
- a step's verification fails twice after one reasonable fix attempt.

- [ ] **Step 1: RED - a planner node that writes a good plan is accepted**

Drive `dispatch_planner`, not a parser helper: the seam under test is "a node ran and left a file
behind", so the double is an executor that writes `plan.json`, never a patched parse function.

```python
async def test_a_valid_plan_json_is_parsed_validated_and_ids_allocated(tmp_path):
    ctx = plan_context_writing(
        tmp_path,
        {
            "goal": "g",
            "entries": [
                {
                    "spec": spec_dict(node_id="ignored"),
                    "op": "work",
                    "args": {},
                    "brief": "b",
                    "output_contract": ["status"],
                    "acceptance": None,
                }
            ],
            "done_when": done_when_dict(),
        },
    )
    out = await dispatch_planner(
        spec=SPEC, goal="g", evidence={}, ctx=ctx, registry=REG, limits=LIMITS, is_root=False, allocate_id=ids()
    )
    assert isinstance(out, Planned)
    assert out.plan.entries[0].spec.node_id == "n-0001"  # ALLOCATED, not the model's word
```

- [ ] **Step 2: RED - the three ways a planner node fails, each its own record**

```python
async def test_unparseable_json_is_not_planned(tmp_path):
    out = await dispatch_planner(..., ctx=plan_context_writing(tmp_path, raw="{not json"))
    assert isinstance(out, NotPlanned) and any("parse" in r for r in out.reasons)


async def test_a_missing_plan_file_is_not_planned(tmp_path):
    out = await dispatch_planner(..., ctx=plan_context_writing(tmp_path, raw=None))
    assert isinstance(out, NotPlanned) and any(PLAN_FILENAME in r for r in out.reasons)


async def test_validate_plan_reasons_are_carried_verbatim(tmp_path):
    ctx = plan_context_writing(tmp_path, plan_naming_op("teleport"))
    out = await dispatch_planner(...)
    assert isinstance(out, NotPlanned) and any("teleport" in r for r in out.reasons)
```

The third is the one that matters: a refusal must reach the caller as the validator's OWN reasons,
not a flattened "planning failed". The parent plan branches on those strings.

- [ ] **Step 3: RED - the planner is told what ops exist**

```python
def test_the_prompt_names_the_registered_ops_and_the_schema():
    text = PLANNER_PROMPT.format(schema=json.dumps(plan_json_schema()), ops=sorted(REG.names()))
    assert "gate:make-test" in text and "apply" not in text  # apply is never registered
    assert "done_when" in text
```

`apply` absent is the assertion that carries information: DECISIONS item 8 says a planner may not
emit it, and refusal-by-absence only helps after the fact. Telling the planner is the cheap half.

- [ ] **Step 4: RED - `judge` is UNREGISTERED until component 5 builds it**

DECIDED at Checkpoint B (user, 2026-08-29). Once `plan` has a real body, `judge` would be the only
op that validates by NAME and then raises at dispatch - a plan naming it is accepted, and the run
explodes mid-flight, after spend. Unregistering moves that refusal to plan-accept time, before any,
which is what refusal-by-absence is for and exactly how `apply` is already handled (DECISIONS
item 8).

It also retires an unverified flag rather than shipping a guess. `judge` is registered today with
`can_change_state=True` above a comment saying UNVERIFIED, "no body yet, so no emitter was read for
this". Decision 4's rule keys on precisely that flag, so if a judge in fact changes no state - it
reads artifacts and emits a verdict - a wrong True lets a root `done_when` of gate-rc plus
judge-verdict satisfy the rule with no state-changing work on any satisfying path, which is the
loophole decision 4 exists to close. Component 5 sets the flag by READING the emitter it builds.

```python
def test_judge_is_not_registered_until_component_5():
    assert "judge" not in REG.names()

def test_a_plan_naming_judge_is_refused_whole_before_any_dispatch():
    out = validate_plan(plan_with(entries=[entry(op="judge")]), registry=REG, ...)
    assert isinstance(out, Refused) and any("judge" in r for r in out.reasons)

def test_the_planner_prompt_says_judging_is_not_available_yet():
    """Without this the refusal reads as a typo. The planner is TOLD, the same way it is
    told which ops exist - refusal by absence is the backstop, not the interface."""
    assert "judge" in PLANNER_PROMPT and "not yet available" in PLANNER_PROMPT
```

Note for whoever writes Task 31's successor: the probe asserted `plan` and `judge` were both
registered. `judge` moving out is this decision, not a regression.

- [ ] **Step 4b: GREEN - implement, and turn `plan`'s body into a GUARD**

CORRECTED while executing, 2026-08-30. This step said `plan` should get a real `_build_planner`
calling `dispatch_planner`, leaving the registry with no raising body at all. That is wrong, and
design section 3.3's own scheduler says why:

    if entry.op == "plan":  dispatch the planner, then execute(sub)   # recursion
    else:                   dispatch(registry[entry.op], entry)

A `plan` entry is SPECIAL-CASED by the loop and never dispatched through the registry. A registry
body calling `dispatch_planner` would therefore be half a mechanism: it would plan a sub-plan and
discard it, because nothing at that layer can execute the result. The loop that can is Task 33,
and it reaches `dispatch_planner` directly.

So `plan` stays registered (a nested sub-goal must VALIDATE) with a body that raises, but the
raise changes meaning - from "not yet wired" to "the scheduler failed to special-case this",
which is a real invariant guard rather than a placeholder. `_build_not_yet_wired` becomes
`_build_plan_entry_guard`. `judge` is simply not registered.

Its `can_change_state=True` also stops being UNVERIFIED and gets a reason: a plan entry stands for
the subtree it expands into, which runs the state-changing work. Recorded limit: a sub-plan could
in principle be all gates, and what catches that is the root rule binding the SUB-plan when it is
validated on its own terms.

Also corrected here: `dispatch_planner` needs a `graph` parameter. The signature in Interfaces
above omitted it while `validate_plan`, which this function calls, requires it to check an entry's
deps against already-admitted nodes.

- [ ] **Step 5: Mutation-check the id allocation**

In a scratch COPY of `planner.py` (copy the file aside first; `git checkout --` would discard
uncommitted work), make the parsed plan keep the model's `node_id`. Step 1's assertion must FAIL.
Restore from the copy.

- [ ] **Step 6: Gate and commit**

`env -u FORCE_COLOR make test`; commit on `feat/kernel-m6`:
"Wire the planner op: a planner node's plan.json becomes a validated Plan or typed reasons".

---

### Task 33: the recursive execute loop, and the two condition checks

**Files:**
- Create: `src/agentdag/application/kernel/execute.py`
- Modify: `src/agentdag/domain/policy.py` `RunLimits` - `max_nodes_per_run`'s docstring stops
  saying "parsed, not yet enforced", and `max_plan_depth: int` is ADDED (Checkpoint B)
- Modify: `src/agentdag/policy/tier-policy.yaml` `run_limits` - the same comment, plus
  `max_plan_depth: 5`
- Modify: `tests/test_policy.py` - the shipped-values test pins `run_limits`, so a new key
  belongs in it; it is the test that is MEANT to go red when the table moves
- Test: `tests/test_kernel_execute.py`

**Interfaces:**
- Consumes: `Planned` / `dispatch_planner` (Task 32); `evaluate`, `evaluate_holds_while`
  (`domain/condition.py`, `domain/plan.py`); `OpRegistry.get`, `PlanContext`, `Body`
  (`registry.py`); `Coordinator._map_semaphore` (`context.py:194`, the RUN-wide bound);
  `NodeStatus`; `Isolation` (`domain/models.py:116`).
- Produces:

```python
# application/kernel/execute.py
class RunNodeBudgetExceeded(KernelError):
    """The run tried to dispatch more nodes than max_nodes_per_run allows."""

class PlanDepthExceeded(KernelError):
    """A nested plan went deeper than max_plan_depth allows (Checkpoint B, decided)."""

class NodeBudget:
    """How many nodes this RUN has dispatched, shared across every plan and recursion.

    Mutable and passed down deliberately: a frozen count would have to be threaded back
    up through every recursion, and the one thing this must not be is per-plan.
    """
    def spend(self, n: int = 1) -> None:      # raises RunNodeBudgetExceeded past the limit
    @property
    def spent(self) -> int

@dataclass(frozen=True, slots=True)
class Executed:
    records: Mapping[str, ResultRecord]     # every terminal record of this subtree
    done: bool                              # plan.done_when evaluated true
    fired: Condition | None                 # the condition that refuted, if one did
    fired_on: str | None                    # the node_id whose record refuted it

async def execute_plan(
    plan: Plan, *, ctx: PlanContext, registry: OpRegistry, limits: RunLimits,
    depth: int, spent: NodeBudget,
) -> Executed:
    """Run one plan's entries to terminal, recursing on op="plan" entries.

    Returns rather than re-plans: the trigger/barrier/re-dispatch path is Task 34/35.
    """
```

**Out of scope** - do NOT touch, though they look related:
- Re-planning. This loop REPORTS a refuted condition in `Executed.fired` and stops the subtree; it
  never re-dispatches a planner. Task 35 owns that, and building it here would mean the barrier
  (Task 34) does not exist yet, so nodes would be re-planned around while still in flight.
- `Coordinator.map` and its own semaphore use (`context.py:645`) - the map fan-out keeps working
  as it does. This loop takes the SAME `_map_semaphore`; a second semaphore would silently double
  the host's parallel bound, which is the one thing `parallel` is documented to prevent
  (`context.py:190-194`).
- `max_replans` - still parsed and never enforced after this task. Task 35 is where it binds.

**STOP conditions** - stop and report rather than improvise, if:
- `Coordinator._map_semaphore` is no longer the run-wide bound (a per-map semaphore would mean
  this loop must not share it, and that is a design question, not a workaround);
- `NodeSpec.deps` is not what "deps satisfied" should read (Task 30's validator already checks
  deps against earlier entries, so a second notion of deps here means the two disagree);
- recursion depth turns out to need its own limit (see the note under Step 5) - report it, do not
  invent a `max_plan_depth`;
- a step's verification fails twice after one reasonable fix attempt.

- [x] **Step 1: RED - ready entries dispatch, dependents wait**

```python
async def test_an_entry_waits_for_its_deps_and_then_runs(tmp_path):
    plan = plan_with(entries=[entry(op="work", node_id="a"), entry(op="work", node_id="b", deps=["a"])])
    out = await execute_plan(plan, ctx=recording_ctx, registry=REG, limits=LIMITS, depth=0, spent=NodeBudget())
    assert recording_ctx.order == ["a", "b"]
    assert set(out.records) == {"a", "b"}
```

- [x] **Step 2: RED - the two condition checks, and what each does**

```python
async def test_a_refuted_acceptance_stops_the_subtree_and_names_what_fired():
    plan = plan_with(entries=[entry(op="gate:make-test", node_id="g",
                                    acceptance=Compare(ref=FieldRef(entry="g", field="rc"),
                                                       op="==", value=0))],
                     done_when=always_true())
    out = await execute_plan(plan, ctx=ctx_where("g", rc=1), ...)
    assert out.done is False and out.fired is not None and out.fired_on == "g"

async def test_a_refuted_holds_while_stops_the_subtree_even_when_the_entry_passed():
    ...
    assert out.fired_on == "n0"          # the record that landed, not the guard's own owner

async def test_a_node_merely_finishing_is_not_a_trigger():
    """Design section 4's first line. A DONE record with no acceptance and a satisfied
    holds_while must leave `fired` None - otherwise every completion re-plans."""
    out = await execute_plan(plan_with(entries=[entry(op="work")]), ...)
    assert out.fired is None
```

- [x] **Step 3: RED - recursion on a `plan` entry**

```python
async def test_a_plan_entry_recurses_and_its_records_join_the_parent():
    inner = plan_with(entries=[entry(op="work", node_id="i")])
    plan = plan_with(entries=[entry(op="plan", node_id="p", args={"goal": "sub"})])
    out = await execute_plan(plan, ctx=ctx_planning(inner), ...)
    assert "i" in out.records and "p" in out.records
```

- [x] **Step 4: RED - `max_nodes_per_run` finally binds**

```python
async def test_the_run_node_budget_is_enforced_across_plans():
    limits = LIMITS.model_copy(update={"max_nodes_per_run": 2})
    plan = plan_with(entries=[entry(op="work", node_id=f"n{i}") for i in range(3)])
    with pytest.raises(RunNodeBudgetExceeded):
        await execute_plan(plan, ..., limits=limits, spent=NodeBudget())


async def test_the_budget_counts_across_a_recursion_not_per_plan():
    """Two plans of two entries each, limit 3: the budget is a RUN total, so the fourth
    dispatch raises even though no single plan exceeds max_nodes_per_plan."""
```

The second test is the one that would catch a per-plan counter wearing a run-level name. Note in
the module docstring that `max_nodes_per_run` was parsed and never enforced from Task 30 until
here, and update `domain/policy.py`'s field docstring and the yaml comment in the same commit -
both currently say a later task's job, and this is that task.

- [x] **Step 5: NOT BUILT HERE - DECIDED by the user 2026-08-30, it is component 8's** (was: RED - the dispatch loop reads `spec.isolation`)

**CORRECTED while executing, 2026-08-30. This step's premise is false and the step was NOT built.**
The claim below is that "Checkpoint A folded its enforcement into this component". Three sources in
this same plan say otherwise:

* **Checkpoint A's own result item 4** calls `NodeSpec.isolation` parsed-but-never-enforced a real
  defect and assigns it its **own worktree** - explicitly NOT this component.
* **The mid plan's component 8** is "Where a node runs, and what its environment is made of"
  (decision 6). Component 3, which Tasks 32 and 33 build, is the planner op and the execute loop.
* **Checkpoint A's result item 2**, which was probe-RAN and adversarially reviewed, says where a
  node runs is expressed through an **op's ARGS model** (`per_entry_worktree_n20`: 21 entries,
  twenty naming a different worktree, ACCEPTED). Enforcing `spec.isolation` as dispatch placement
  here would stand up a SECOND mechanism competing with the one that probe validated.

Re-checked at ground truth 2026-08-30: `uv run ~/.claude/skills/toolbox/tools/enforced.py isolation
--root src/` still exits 1 (declared at `models.py:260`, in the journal key, read by nothing).
Design section 3.3's scheduler has no placement step, and section 10's cwd row is the isolation
SCAN (`context.py:262`), a different mechanism.

So the loop dispatches every entry in `ctx.cwd`, and `execute.py`'s module docstring says so. What
the user has to settle before component 8 is written: which of the two mechanisms carries "where
this entry runs", and whether any part of it belongs in this loop.

**DECIDED (user, 2026-08-30): component 8 owns it.** Task 33 is accepted as shipped with every
entry running in `ctx.cwd`; `spec.isolation` stays parsed-never-enforced until component 8 picks
the mechanism. Do not re-open this inside component 3. The original step text follows.

`Isolation` (`models.py:116`) is declared, is part of the node key (`keys.py:39`) and today NOTHING
reads it to decide anything. Checkpoint A folded its enforcement into this component, because this
loop is the code that decides where an entry runs.

```python
async def test_isolation_worktree_gets_a_worktree_and_none_does_not():
    plan = plan_with(entries=[entry(op="work", node_id="w", isolation=Isolation.WORKTREE),
                              entry(op="work", node_id="n", isolation=Isolation.NONE)])
    out = await execute_plan(plan, ctx=recording_ctx, ...)
    assert recording_ctx.prepared == {"w": Isolation.WORKTREE, "n": Isolation.NONE}

async def test_isolation_dir_is_not_silently_treated_as_worktree():
    """The enum has three members and only two behaviours are obvious; assert the third
    explicitly so a two-branch implementation cannot pass."""
```

- [x] **Step 5b: RED - `max_plan_depth` bounds recursion, with its own name**

DECIDED at Checkpoint B (user, 2026-08-29): depth gets its OWN run-limit key rather than sharing
`max_replans`. The mid plan's component 3 says "depth is counted per plan against `max_replans`";
design section 4 spends `max_replans` on RE-PLANS per plan. Two quantities, and welding them to
one value would make tuning re-plan tolerance silently change how deep a plan may nest.

Add `max_plan_depth: int` to `RunLimits` and to `run_limits` in `tier-policy.yaml`, default 5, and
ENFORCE it here. Where 5 comes from, stated plainly because this repo has just been bitten by a
limit whose justification outlived it: it is NOT calibrated on anything. No run has nested a plan
yet, so there is no distribution to read. It is a runaway stop, chosen on the reasoning that a goal
needing more than five levels of sub-planning has a decomposition problem a deeper limit will not
fix. Unlike `max_nodes_per_plan` it is not measuring a fleet, so being wrong by 2x costs a clear
error message rather than a refused legitimate plan. Revisit it against the first real nested run,
and if it ever refuses a plan a human would have allowed, that is the signal it was too low. It is enforced in the same task that adds it, deliberately: this repo has just
spent a session on two knobs that were parsed and never enforced, and a third would be a choice
rather than an oversight.

```python
async def test_nesting_past_max_plan_depth_raises_with_its_own_name():
    limits = LIMITS.model_copy(update={"max_plan_depth": 2})
    with pytest.raises(PlanDepthExceeded):
        await execute_plan(plan_nesting(3), ..., limits=limits, depth=0)


async def test_depth_is_bounded_before_the_node_budget_is_spent():
    """The whole point of a separate key: a nesting runaway must report NESTING, not
    'the run tried to dispatch more nodes than allowed' after burning the budget. Give
    it a generous node budget and a small depth, and assert WHICH error arrives."""
    limits = LIMITS.model_copy(update={"max_plan_depth": 2, "max_nodes_per_run": 1000})
    with pytest.raises(PlanDepthExceeded):
        await execute_plan(plan_nesting(50), ..., limits=limits, depth=0)
```

The second test is the one that earns the key. Without it, an implementation that bounds depth
only through `max_nodes_per_run` passes the first test whenever the budget happens to be small.

- [x] **Step 6: GREEN, then gate and commit**  DONE 2026-08-30, `7ea3fcc`, gate green, 1025 tests

`env -u FORCE_COLOR make test`; commit on `feat/kernel-m6`:
"Add the recursive execute loop: ready entries, two condition checks, a run-wide node budget".

---

### Task 34: the stop notice to a subtree, and the barrier

**Files:**
- Create: `src/agentdag/application/kernel/subtree.py`
- Modify: `src/agentdag/adapters/kernel/executor_claude.py:957` (the `is_stopping` predicate)
- Modify: `src/agentdag/application/kernel/dispatch.py` / `ports.py` as needed to carry the
  predicate to the executor - the port change is part of THIS task, not assumed
- Test: `tests/test_kernel_subtree.py`, `tests/test_executor_stop_notice.py`

**Interfaces:**
- Consumes: `inject_stop_notice(is_stopping, *, handover_path)` (`hooks_claude.py:168`);
  `_Handover` and `HANDOVER_GRACE_TURNS = 3` (`executor_claude.py:504`); `NodeStatus`.
- Produces:

```python
# application/kernel/subtree.py
class StopScope:
    """Which nodes are being asked to stop, readable from another task's coroutine.

    One object per subtree. `is_stopping(node_id)` is what the executor's hook reads on
    every matched tool use, so it must be cheap and must never block.
    """
    def enter(self, node_id: str) -> None
    def leave(self, node_id: str, status: NodeStatus) -> None
    def request_stop(self) -> frozenset[str]      # returns the ids notified, for the journal
    def is_stopping(self, node_id: str) -> bool
    def in_flight(self) -> frozenset[str]

async def barrier(scope: StopScope, *, deadline_bound_s: float) -> frozenset[str]:
    """Wait until every node in `scope` is terminal. Returns the ids still in flight when
    the bound ran out - EMPTY is the success case.

    DECIDED at Checkpoint B (user, 2026-08-29): the bound is DERIVED, never a new knob.
    `deadline_bound_s` is the largest remaining `deadline_s` among the in-flight nodes plus
    BARRIER_SLACK_S, because Task 21 already enforces every node's deadline in wall clock
    (`executor_claude.py:1313` `_deadline_exceeded`). So a node outliving this bound does not
    mean the barrier was impatient - it means deadline enforcement itself failed, which is
    precisely what the returned set should report. An operator-set ceiling would instead have
    to be guessed, and any value below a node's own deadline would fail subtrees whose nodes
    were about to terminate normally.
    """

BARRIER_SLACK_S: float = 30.0
"""Room past the last deadline for the interrupt to land and the record to be written.
Nothing rests on the exact value: it is slack on a bound that is already correct, not the
bound itself."""

def deadline_bound(scope: StopScope, graph: Mapping[str, NodeSpec], now: datetime) -> float:
    """Largest remaining deadline among the in-flight nodes, plus BARRIER_SLACK_S."""
```

**Correction to the sketch above, as built (2026-08-30).** `now` alone cannot yield a REMAINING
deadline: nothing on `NodeSpec` says when a node started. The start had to come from somewhere, and
it comes from the scope - `StopScope.enter(node_id, at)` records membership and the start instant in
ONE call, so a caller cannot enter a node and forget to stamp it. `deadline_bound` reads them back
through a new `in_flight_since()` snapshot, giving:

    def enter(self, node_id: str, at: datetime) -> None: ...
    def in_flight_since(self) -> Mapping[str, datetime]: ...
    def deadline_bound(scope, graph, *, now: datetime, ceiling_s: float | None = None) -> float: ...

This does NOT put a clock in `StopScope`: it takes an instant, never reads one, so `is_stopping`
stays cheap and non-blocking on every matched tool use. A first version instead took a separate
`started_at` mapping alongside, which left "enter and stamp together" as a docstring contract and
needed a decided absent case (a node with no recorded start fell back to its whole deadline). Both
are gone - an in-flight node without a start is now unrepresentable, so the fallback was deleted
rather than kept as defence against an impossibility. The one absent case that remains is a node
with no deadline AND no ceiling, which still RAISES, because there no number exists at all.

`at` must be the instant the node's DEADLINE begins, not when it was queued: the executor measures
`clock.now() - dispatch_started` against `deadline_s`, so stamping at queue time would count the
wait for a parallel-bound slot against the node and under-estimate the bound. Step 2 must therefore
call `scope.enter` inside `_dispatch_leaf` AFTER the slot is acquired, never in `_launch_ready`.

**Out of scope** - do NOT touch, though they look related:
- `_Handover`'s own arming on the context ceiling (`executor_claude.py:561` `observe`). That is
  decision 14's ceiling path and it already works; this task ORs a second reason to stop into the
  same predicate, and changing `observe` would couple two triggers that must stay separable.
- `HANDOVER_GRACE_TURNS`. Three is MEASURED (58 dispatches, `handover-grace-expiry.md`); re-tuning
  it because a subtree stop feels more urgent than a context ceiling would discard that.
- `Coordinator.cancel` / `cancel.py` - cancelling a RUN is a different verb with a different
  record (`CANCELLED`). A stopped node is not cancelled: it hands over and its work is evidence.
- Re-dispatch. Task 35.

**STOP conditions** - stop and report rather than improvise, if:
- `is_stopping` at `executor_claude.py:957` is no longer a zero-argument callable closed over the
  node's own `_Handover` (the whole design of this task is "OR a second predicate into that one");
- the executor port cannot carry a per-node predicate without a signature change that ripples
  past the files listed above;
- the barrier needs to interrupt rather than wait - report it. Design constraint 2 says a stopped
  node is not killed, and this task must not be the place that quietly reverses that;
- a step's verification fails twice after one reasonable fix attempt.

- [x] **Step 1: RED - the predicate is per node and flips for the whole subtree**  DONE 2026-08-30, `ee6fb7f`

```python
def test_request_stop_covers_every_in_flight_node_and_nothing_else():
    scope = StopScope()
    scope.enter("a")
    scope.enter("b")
    scope.leave("b", NodeStatus.DONE)
    assert scope.request_stop() == {"a"}
    assert scope.is_stopping("a") and not scope.is_stopping("b")


def test_a_node_entering_after_the_stop_is_already_stopping():
    """A late entrant must not slip past the notice: the subtree is stopping, not the
    membership list frozen at the moment of the call."""
    scope = StopScope()
    scope.request_stop()
    scope.enter("late")
    assert scope.is_stopping("late")
```

- [x] **Step 2: RED - the executor reads BOTH reasons**  DONE 2026-08-30, gate green, 1066 tests

```python
async def test_the_stop_notice_fires_on_a_subtree_stop_with_no_context_pressure():
    """The node is nowhere near its ceiling, so `handover.armed` is False. Only the
    subtree predicate can put the notice in front of the model."""
    scope = StopScope()
    scope.enter("n")
    scope.request_stop()
    hook = built_hook(node_id="n", scope=scope, usage_far_below_ceiling=True)
    out = hook(tool_use_event())
    assert "handover" in out["hookSpecificOutput"]["additionalContext"]


async def test_the_context_ceiling_still_fires_with_no_subtree_stop():
    """The pre-existing path must keep working: this is the control that says the OR did
    not replace decision 14's trigger with the new one."""
```

- [x] **Step 3: RED - the barrier waits for terminal, and reports rather than lying**  DONE 2026-08-30, `ee6fb7f`, reworked in `3729015`/`a459217`

```python
async def test_the_barrier_returns_empty_once_every_node_is_terminal():
    ...
    assert await barrier(scope, deadline_bound_s=5.0) == frozenset()


async def test_the_barrier_reports_who_was_still_running_on_timeout():
    scope = StopScope()
    scope.enter("stuck")
    scope.request_stop()
    assert await barrier(scope, deadline_bound_s=0.05) == {"stuck"}


def test_the_bound_is_derived_from_the_in_flight_deadlines_not_a_constant():
    """Checkpoint B: the bound must MOVE with the nodes. A subtree of 60s nodes and one
    of 3600s nodes must not get the same bound, or it is a constant wearing a
    derivation's name."""
    short = deadline_bound(scope_of(deadline_s=60), graph, now)
    long_ = deadline_bound(scope_of(deadline_s=3600), graph, now)
    assert long_ - short == pytest.approx(3540, abs=1)  # the DIFFERENCE, not the values
```

The second test is the important one. A barrier that returned success on timeout would let Task 35
re-plan around a node that is still writing to the worktree, which is the exact race the barrier
exists to prevent. Report the timeout; never treat it as done.

- [x] **Step 4: RED - grace is honoured, not bypassed**  DONE 2026-08-30, gate green, 1066 tests

```python
async def test_a_stopped_node_gets_its_grace_before_the_interrupt():
    """HANDOVER_GRACE_TURNS requests after the notice, then interrupt - the same measured
    grace the ceiling path uses. Asserted as a COUNT, so a zero-grace immediate interrupt
    fails here rather than showing up as a lost handover under load."""
    assert interrupts_after_requests(scope_stop=True) == HANDOVER_GRACE_TURNS
```

- [ ] **Step 5: GREEN, then gate and commit**

`env -u FORCE_COLOR make test`; commit on `feat/kernel-m6`:
"Add the subtree stop scope and the barrier: a trigger notifies, it never kills".

---

### Task 35: re-dispatch - the new plan replaces the unexecuted entries

**Files:**
- Modify: `src/agentdag/application/kernel/execute.py` (the loop now re-plans instead of returning
  on a refuted condition)
- Modify: `src/agentdag/domain/journal.py` (+ `PlanAcceptedLine`, `PlanInvalidatedLine`,
  `SubtreeDoneLine`; add all three to the `JournalLine` union at `journal.py:217`)
- Modify: `src/agentdag/domain/policy.py` and `src/agentdag/policy/tier-policy.yaml` -
  `max_replans` stops being "parsed, not yet enforced"
- Modify: `src/agentdag/application/kernel/notify.py` (forward the new lines to the sinks)
- Modify: `src/agentdag/application/kernel/run.py:91` `run_coordinator` (call `run_root`)
- Test: `tests/test_kernel_replan.py`, `tests/test_journal.py` (the three lines round-trip)

**Interfaces:**
- Consumes: `StopScope`, `barrier` (Task 34); `dispatch_planner`, `Planned`, `NotPlanned`
  (Task 32); `Executed` (Task 33); `RunLimits.max_replans`.
- Produces:

```python
# application/kernel/execute.py  (added to Task 33's module)
class ReplanLimitExceeded(KernelError):
    """A plan hit max_replans; the record is FAILED and the PARENT plan branches on it."""

@dataclass(frozen=True, slots=True)
class Cause:
    """What the re-dispatched planner is told fired, with values - never prose."""
    condition: Condition
    node_id: str
    values: Mapping[str, object]          # the fields the condition read, and what they were

async def run_root(
    *, goal: str, ctx: PlanContext, registry: OpRegistry, limits: RunLimits,
    graph: Mapping[str, NodeSpec],
) -> Executed:
    """Plan and execute the ROOT goal - the one plan with no parent to branch on it.

    Called by `run_coordinator` (`application/kernel/run.py:91`), which today takes a
    workflow name; `run start` accepting a GOAL is component 6's CLI change, so this task
    adds the function and the wiring, not the command.

    Root refusal ladder, decided at Checkpoint B: re-plan bounded by `max_replans` with the
    validator's reasons as the cause, then SUSPEND into approve rather than fail.
    """

# execute_plan gains two parameters here. Task 33 shipped it without them; this is the
# whole signature after this task, so an implementer reading only this task has it:
async def execute_plan(
    plan: Plan, *, ctx: PlanContext, registry: OpRegistry, limits: RunLimits,
    depth: int, spent: NodeBudget,
    # NEW: no timeout knob. The barrier's bound is DERIVED per subtree from the in-flight
    # nodes' own deadlines (Task 34 `deadline_bound`), so execute_plan needs the graph to
    # read those deadlines off, not a duration.
    graph: Mapping[str, NodeSpec],
    replans: int = 0,                     # NEW: re-plans already spent on THIS plan
) -> Executed:
```

`ReplanLimitExceeded` is raised INSIDE a subtree and never escapes `execute_plan`: the boundary
catches it and returns an `Executed` whose entry record is FAILED, which is what Step 3's second
test pins. It is an exception rather than a return value only so the deep recursion does not have
to thread an exhaustion flag back through every level.

**Out of scope** - do NOT touch, though they look related:
- Completed entries. Design section 4 step 4: they stay in the journal and the new plan references
  them through `deps` or does not. Re-running a completed entry because the plan around it changed
  would spend a node to reproduce a record already on disk.
- A SIBLING subtree. It is affected only through a premise its PARENT declared (`holds_while` on
  the parent plan). Stopping a sibling because its neighbour re-planned is the "re-plan wrong
  rather than late" direction the design explicitly rejects.
- `run steer` - trigger 3 is component 6's CLI verb. This task takes the trigger as an input
  (a `steer` record landing in the plan's records); it does not add the command.
- The salvage handover's READ path. `domain/handover.py` writes; nothing reads it back, and that
  gap belongs to component 7. Here, pass the handover records that already exist as evidence and
  note in the docstring that the salvage half is component 7's.

**STOP conditions** - stop and report rather than improvise, if:
- component 7 has not landed and the handover records are not readable as evidence: pass what
  exists, and report the gap rather than inventing a reader here;
- the barrier returns a non-empty set (nodes still in flight): that is Task 34's timeout, and
  re-planning on top of it is the race the barrier exists to prevent. Fail the subtree instead;
- a step's verification fails twice after one reasonable fix attempt.

- [x] **Step 1: RED - a refuted acceptance re-plans once, and only the unexecuted entries change**  DONE 2026-08-30, `6f0a6ba`

```python
async def test_the_new_plan_replaces_unexecuted_entries_and_keeps_completed_records():
    plan = plan_with(entries=[entry(op="work", node_id="done_one"),
                              entry(op="gate:make-test", node_id="g", acceptance=rc_is_zero()),
                              entry(op="work", node_id="never_ran", deps=["g"])])
    out = await execute_plan(plan, ctx=ctx_where("g", rc=1, replan_gives=new_plan), ...)
    assert "done_one" in out.records                    # kept, not re-dispatched
    assert ctx.dispatched.count("done_one") == 1
    assert "never_ran" not in ctx.dispatched            # replaced before it ever ran
```

- [x] **Step 2: RED - the planner is told what fired, with values**  DONE 2026-08-30, `6f0a6ba`

```python
async def test_the_cause_carries_the_condition_the_node_and_the_values():
    out = await execute_plan(..., ctx=ctx_where("g", rc=1))
    cause = ctx.last_planner_evidence["cause"]
    assert cause.node_id == "g" and cause.values == {"g.rc": 1}
```

A re-plan that says only "something failed" makes the planner guess at what to fix. Assert the
VALUES, not just that a cause object exists.

```python
async def test_the_planner_gets_the_records_the_previous_plan_and_the_handovers():
    """Design section 4 step 3 lists four things, and a cause alone is one of them. The
    previous plan is what stops the planner re-emitting the entries that just failed."""
    ev = ctx.last_planner_evidence
    assert ev["previous_plan"].goal == plan.goal
    assert set(ev["records"]) == {"done_one", "g"}
    assert [h.node_id for h in ev["handovers"]] == ["in_flight_one"]
```

- [~] **Step 2b: RED - trigger 3, a steer record, and the sibling rule**  SIBLING RULE DONE 2026-08-30, `6ef0b2f`; STEER BLOCKED, see below

Component 4 names THREE triggers. Acceptance and `holds_while` are Steps 1 and 2; the third is a
person's `steer` record landing in the plan (section 7). It is driven here as a RECORD, because
the CLI verb that writes one is component 6.

```python
async def test_a_steer_record_re_plans_the_subtree_it_lands_in():
    out = await execute_plan(plan, ctx=ctx_where_steer_lands("guidance text"), ...)
    assert ctx.planner_dispatches == 2
    assert ctx.last_planner_evidence["cause"].node_id == "steer"

async def test_a_sibling_subtree_is_not_stopped_when_its_neighbour_re_plans():
    """The design's safe direction (section 4, last table row): a sibling is affected ONLY
    through a premise its PARENT declared in `holds_while`. Stopping it because its
    neighbour re-planned would re-plan WRONG rather than LATE."""
    parent = plan_with(entries=[entry(op="plan", node_id="left"), entry(op="plan", node_id="right")],
                       holds_while=None)
    await execute_plan(parent, ctx=ctx_where_left_refutes, ...)
    assert "right" not in ctx.stop_requested

async def test_a_parent_holds_while_DOES_stop_both_subtrees():
    """The control for the test above. Without it, an implementation that never stops any
    sibling passes, and the premise-at-the-parent rule would be untested in the direction
    where it must ACT."""
    parent = plan_with(entries=[...], holds_while=shared_premise())
    await execute_plan(parent, ctx=ctx_where_the_premise_refutes, ...)
    assert {"left", "right"} <= ctx.stop_requested

- [x] **Step 3: RED - `max_replans` binds, and exhaustion is a record the parent reads**  DONE 2026-08-30, `6f0a6ba` + `6e07cfd`

```python
async def test_replans_are_bounded_per_plan():
    limits = LIMITS.model_copy(update={"max_replans": 2})
    out = await execute_plan(always_refuting_plan, ..., limits=limits)
    assert ctx.planner_dispatches == 2 + 1            # the original plan, then two re-plans
    assert out.done is False

async def test_exhaustion_is_a_failed_record_not_a_raise_through_the_parent():
    """Design section 4 step 3: exhaustion is a failed record the PARENT plan branches on.
    A raise that escapes execute_plan would take the whole run down with it."""
    inner_exhausts = plan_with(entries=[entry(op="plan", node_id="p")])
    out = await execute_plan(inner_exhausts, ...)
    assert out.records["p"].status is NodeStatus.FAILED
```

Update `domain/policy.py`'s `max_replans` docstring and the yaml comment in this same commit; both
say "parsed, not yet enforced by anything - a later task's job", and this is that task.

- [x] **Step 4: RED - the barrier is used, and a timeout does not re-plan**  DONE 2026-08-30, `6f0a6ba`

```python
async def test_in_flight_nodes_are_stopped_and_waited_for_before_the_planner_runs():
    assert ctx.order.index("stop_requested") < ctx.order.index("barrier_returned")
    assert ctx.order.index("barrier_returned") < ctx.order.index("planner_dispatched")


async def test_a_barrier_timeout_fails_the_subtree_rather_than_re_planning():
    out = await execute_plan(..., ctx=ctx_with_a_stuck_node, graph=graph_of(deadline_s=0.05))
    assert ctx.planner_dispatches == 1  # the original only; no re-plan
    assert out.done is False
```

- [x] **Step 4b: the ROOT plan has no parent, so it re-plans then asks**  DONE 2026-08-30/31 - `fe8de31` the ladder (`application/kernel/root.py`, nine arms, ten mutations verified failing by name) and `fd3b625` the WIRING the sketch also asks for (`plan-goal`, the `Policy` port's whole `run_limits`, the registry injected onto the `Coordinator`). Eleven arms in `tests/test_kernel_root.py`; `agentdag run start plan-goal --arg goal="..."` reaches it

DECIDED at Checkpoint B (user, 2026-08-29). A nested plan that will not validate becomes a FAILED
record its parent branches on (Step 3). The root has no parent. It takes the same ladder the rest
of this project already uses for "retry once, then ask" - design 2.3 rule 5, which re-dispatches a
blocked node once and suspends into `approve` rather than climbing further.

So: re-dispatch the root planner with the validator's reasons as the cause, bounded by
`max_replans`; on exhaustion SUSPEND into `approve`, not fail. A suspended run stays resumable and
keeps every record it earned, which is the same reason `on_rate_limit: suspend_run` is a suspend
rather than a failure in `tier-policy.yaml`.

```python
async def test_a_root_plan_that_will_not_validate_is_re_planned_with_the_reasons():
    out = await run_root(goal="g", ctx=ctx_where_first_plan_names("teleport"))
    assert "teleport" in ctx.planner_evidence[1]["cause"].values["reasons"]
    assert ctx.planner_dispatches == 2


async def test_root_replan_exhaustion_suspends_rather_than_failing():
    limits = LIMITS.model_copy(update={"max_replans": 1})
    out = await run_root(goal="g", ctx=ctx_where_every_plan_is_invalid, limits=limits)
    assert out.status is NodeStatus.BLOCKED  # suspended into approve, resumable
    assert ctx.approve_payloads[-1].question  # a person is asked, with the reasons


async def test_a_valid_root_plan_never_reaches_the_approve():
    """The control. Without it an implementation that suspends unconditionally passes the
    test above, and every run would stop for a human on its first plan."""
    out = await run_root(goal="g", ctx=ctx_where_the_first_plan_is_good)
    assert ctx.approve_payloads == [] and ctx.planner_dispatches == 1
```

- [x] **Step 5: RED - the three journal lines round-trip, and the loop emits them**  DONE 2026-08-30, `e7cc3f8` (the lines) + `d9827cc` (the emission) + `b7ca0e9` (a re-planned subtree's verdict is keyed to the plan that RAN)

```python
def test_the_three_new_lines_parse_back_to_their_own_types():
    for line in (
        PlanAcceptedLine(key="k", node_id="p", entries=3),
        PlanInvalidatedLine(key="k", node_id="p", reasons=("g.rc == 1",)),
        SubtreeDoneLine(key="k", node_id="p", done=True),
    ):
        assert type(parse_journal_line(line.model_dump_json())) is type(line)
```

Dump with NO arguments. A test passing `by_alias=True` exercises a path production does not use,
and this repo has already shipped a field whose DEFAULT dump was rejected by its own schema.

- [ ] **Step 6: GREEN, then gate and commit**

`env -u FORCE_COLOR make test`; commit on `feat/kernel-m6`:
"Re-plan on a refuted condition: stop the subtree, wait, re-dispatch the planner with the cause".

**What blocks the two open steps (2026-08-30).** Both need a decision, not more building.

*Step 2b's steer half.* There is no steer vocabulary anywhere in the codebase, and the two
plans disagree on its shape: `build-plan-mid.md:634` says `run steer` writes TYPED GUIDANCE TO
THE JOURNAL, while this task says a `steer` RECORD LANDING IN THE PLAN'S RECORDS. Implementing
either means inventing the type that component 6 owns. The sibling-rule half of 2b is built and
mutation-verified; only the trigger is open.

*Step 4b, SETTLED 2026-08-30.* Two of the three "under-specified" items were false alarms that a
lookup closed; one was a real decision, and settling it raised a fourth. All four:

1. **`run_root` RAISES `Suspended`; it returns `Executed` on the ordinary path.** Settled by
   lookup, not judgement: `_drive` (`run.py:215`) is the only thing that writes `state.json`,
   sets the cursor, records the suspend reason and notifies, and it does that in
   `except Suspended`. A returned status reaches none of it, so a suspend that RETURNED would be
   neither resumable nor announced. The sketch's `assert out.status is NodeStatus.BLOCKED` is a
   category error besides - `NodeStatus` is a node's verdict, a run's is `RunStatus`.
2. **The WORKFLOW PROGRAM passes the root planner's `NodeSpec`; `run_root` mints nothing**
   (user, 2026-08-30). Signature: `run_root(*, goal, planner: NodeSpec, ctx, registry, limits,
   graph)`. This is the shape the kernel already has - `dispatch_planner` and `execute_plan` BOTH
   take a planner `NodeSpec` from their caller - and graph A already mints every spec it
   dispatches with explicit literals (`_work_spec`, `graph_a.py:586`). So the tier role, deadline
   and budget "nothing in the task names" are not the kernel's to name.
3. **NOT blocked on component 6** - but HALF FALSE, corrected 2026-08-30 after building it.
   The CLI half stands: `run start` already takes `--arg KEY=VALUE`, repeatable, validated
   through each workflow's own `args_model` (`run.py:260`, `run.py:731`), so a goal is a workflow
   ARGUMENT and no CLI change is owed. The PROGRAM half does not. A workflow program is handed
   `(co, args)` and nothing else, and `run_root` needs two things it cannot get from there: an
   `OpRegistry`, built only by `composition.kernel.build_op_registry`, which the import-linter
   "Clean Architecture layers" contract forbids `application` importing; and a `RunLimits`, which
   IS loaded in production (`tier-policy.yaml` parses into the policy table's `run_limits`) but
   which the `Policy` PORT copies only two of the nine fields out of - `tokens_per_row` and
   `deadline_ceiling_s`, neither of them `max_replans` or any node or depth bound. So `run_root`
   has no production caller, and neither does `execute_plan` (Task 33): a pre-existing gap, and
   the one this step's own sketch already assigned here ("this task adds the function and the
   wiring, not the command"). DONE 2026-08-31 in `fd3b625`, the option the user chose: the
   `Policy` port now carries the whole `run_limits` block rather than two of its nine fields,
   and the registry is injected through `KernelWiring` and `run_coordinator` onto the
   `Coordinator`, the way every other port arrives. The `plan-goal` workflow uses it, so the
   claim this decision made is now true - verified by RUNNING the CLI, not by reading it.
4. **The exhaustion approve offers ABANDON (the no-effect default) or GRANT ANOTHER PLANNING
   ROUND** (user, 2026-08-30), rather than being terminal. The risk in that shape is that a
   decision is FINAL per (`node_id`, `payload_hash`), so a grant recorded under a payload that
   hashes the same next round would be re-served on resume and re-plan unattended, forever. It is
   closed by the payload's own `artefact_refs`, which name the FAILING PLANNER DISPATCH's node
   directory: that path carries the `hash8` of that dispatch's journal key, which differs every
   round because the brief carries the cause. So each exhaustion hashes differently BY
   CONSTRUCTION, no round counter reaches operator-facing prose, and the decider gets a pointer to
   what the planner actually wrote. An arm must pin that two consecutive exhaustions do not share
   a payload hash - that is the assertion the whole shape rests on.

*Step 5's emission, settled 2026-08-30 (`d9827cc`).* The open question was what journal key a
plan entry's line carries. It is the PLANNER DISPATCH's own key, read off the record that
dispatch produced: `ResultRecord.input_hash` IS that key rather than one of its ingredients
(`result-record.schema.json`), so each line joins to that node's `started` and `result` lines.
`SubtreeDoneLine` reads it off the planner's CURRENT record, so a re-planned subtree's verdict
is attributed to the dispatch whose plan actually ran. A plan NOBODY PLANNED emits nothing:
a hand-authored plan handed straight to `execute_plan` has no planner node and no key, and both
fields are required non-empty. Exhaustion of `max_replans` journals its verdict through the same
`_verdict` the returned `Executed` uses - before it raises in a sub-plan, and before it
returns the abandoned subtree at the root (Task 36).

This task's file list named `application/kernel/notify.py` ("forward the new lines to the
sinks"); it was NOT touched and needs nothing. That module carries `RunEvent`/`RunStatus` to an
operator, and knows no journal line at all.

---

### Task 36: the root's CONDITION exhaustion takes the same ladder  DONE 2026-08-31, `1cff6c3`

The task Task 35 flagged and deliberately did not widen into. A root plan the VALIDATOR refuses
had a ladder since step 4b; a root plan that validates, RUNS, and whose condition then refutes
past `max_replans` had none - it raised `ReplanLimitExceededError` out of `execute_plan` and the
run ended FAILED and unresumable, stranding the records a plan that actually ran had earned.

What the task had to settle first, per Task 35's own note: what one granted round BUYS. Settled
with the user 2026-08-31 - another whole `max_replans`, continuing from the plan that was
running, which is what GRANT already means on the validator ladder. One word, one meaning.

Shipped:

- `execute_plan(..., grant_more: GrantMoreReplans | None = None)`, asked at exhaustion. True buys
  another `max_replans`; False abandons and RETURNS a not-done `Executed` carrying the cause and
  the records. ROOT-ONLY BY CONSTRUCTION rather than by a depth test: the recursion into a
  sub-plan does not pass it, so a sub-plan still reports exhaustion to the parent that can branch
  on it, and `grant_more=None` is exactly the old behaviour.
- `root._granted_more` / `_refuted` / `_refuted_question`: its own payload, not a parameterisation
  of the validator one - that asks about a plan nothing ran, evidenced by reasons; this asks about
  a plan that ran, evidenced by a condition and the values it read. Same ABANDON default, same
  two `none`-effect options, sharing `run_root`'s per-launch `asked` guard.
- `max_replans`' docstring and the `tier-policy.yaml` comment both said exhaustion raises. That is
  now the sub-plan half only, and both now say the knob is the size of ONE round rather than a
  root run's total.

Six arms in `tests/test_kernel_root.py`, each mutation-verified failing BY NAME on its own
assertion. The termination arm needed all THREE payload distinguishers mutated before it could
fail, and the brief-distinctness arm needed both of its.

*A counter was written into the re-plan brief and then removed.* It was carried over from
`root._ask`, which needs one because that ladder re-asks with the same goal and reasons. Here it
defends nothing: `_refuted` records the LANDING ENTRY's node id, never the id of the node the
condition referenced, so the admitted-node case that would have justified it does not exist. A
probe over a three-round run showed what actually keeps briefs distinct - the id and values in the
goal text AND the evidence block, which grows by every record the round landed, two independent
renderings both reducing to `NodeIds` never reusing an id. Replaced by an arm on the property.

**Left open, as its own task (user, 2026-08-31).** Every granted round spends from the RUN-wide
node budget, and crossing `max_nodes_per_run` is returned by `_launch_ready` and re-raised by
`_execute`, so it escapes `run_root`: a run granted enough rounds dies the same unresumable death
this task removed, one bound further out. WHAT THAT TASK MUST SETTLE FIRST: whether
`max_nodes_per_run` is a spend ceiling or a runaway guard. Rejected now as out of scope - a
run-wide budget cannot be reset per plan without making the ceiling meaningless.

---

### Task 37: the run-wide NODE BUDGET takes the same ladder  DONE 2026-08-31, `150d888`

The task Task 36 filed against itself. Task 36 made a refuted CONDITION ask instead of raising,
but every granted round spends from the RUN-wide node budget, so a run granted enough rounds still
died on `max_nodes_per_run` - unresumable, stranding its records, which is the death Task 36 was
written to remove, one bound further out.

**The prerequisite, settled by LOOKUP rather than judgement.** Task 36 recorded that this task must
first answer whether `max_nodes_per_run` is a spend ceiling or a runaway guard. The shipped table
already answered it: Checkpoint A raised it 200 -> 1000 because "a ceiling calibrated on what
planners produce refuses legitimate wide work", and `max_nodes_per_plan` at 1000 "fires only on a
pathological plan". A calibrated ceiling was tried and abandoned. It is a RUNAWAY GUARD, so hitting
it is exactly the case a person should judge.

**Where a grant takes effect is the whole design, and it is NOT where it is asked** (user,
2026-08-31). The budget is per-LAUNCH and `_launch_ready` spends BEFORE the dispatch, so a
journal-served replay costs what running it did: by the time the question is asked, the launch has
already charged its way to the ceiling. `with_budget_grants(limits, co=, approve_id=)` therefore
raises the ceiling at RUN START from the grants the journal holds - `base x (1 + grants)` - and the
next launch replays under it and walks past the stop.

Two consequences worth keeping:

- **The budget question needs an approve node of its own** (`a_budget`). Forced, not stylistic: a
  decision is recorded per (node id, payload hash), so the run-start count can only tell budget
  grants from planning grants by node id.
- **A grant buys less than the number offered.** The relaunch re-charges the work already done
  before reaching anything new, so the NET gain is an allowance minus history. The operator text
  says so.

`execute.py` gains `BudgetExhausted`, which returns `None` rather than a bool - unlike
`GrantMoreReplans` there is no "continue" the loop could act on, so it RAISES (`Suspended`, launch
ends resumably) or RETURNS (abandoned). It catches a NESTED plan's exhaustion too: `_settle` turns
the task's exception into the parent's `run.failure` and each level re-raises until it arrives at
the root as one.

Two defects that would have shipped silently, both caught by writing the arm before the code:

- **An abandoned budget must be FORCED not-done.** `_verdict` calls a subtree stopped when a
  condition refuted or a sub-plan was refused, and a spent budget is NEITHER - so a `done_when`
  naming an entry that did land would report the run DONE with half its plan in `unrun`.
- **A GRANT served at the ASK is a wiring failure, not an outcome.** It means a grant is on record
  that run start did not apply. Unguarded, the run re-asks the identical question every launch
  forever, each time appearing to ignore the person's answer.

Five arms, each mutation-verified. The grant arm needed the scale AND the wiring guard mutated
TOGETHER before it failed on its own assertion rather than through the guard - single-layer
mutation read as a pass.

**Still open after this task.** Nothing about the budget. The remaining root question is whether a
resumed run should re-charge its own history at all: the budget counts served dispatches, so a long
run spends its allowance on replay. Changing it means charging AFTER the dispatch instead of before,
which `NodeBudget.spend`'s docstring says is deliberate ("the refusal arrives instead of the spend
rather than after it"), so it is a real decision rather than a fix - not scoped here.

---

## Checkpoint B

Tasks 32 to 35 done: components 3 and 4 are built and the planning loop closes.

The four questions this checkpoint was raised to settle were DECIDED with the user on 2026-08-29,
before the tasks were executed, and each is now written into the task that owns it rather than
left here. Recorded so nobody re-opens them:

| question                            | decided                                                                    | lives in      |
|-------------------------------------|----------------------------------------------------------------------------|---------------|
| recursion depth                     | its own key `max_plan_depth`, default 5, ENFORCED in the task that adds it | Task 33, 5b   |
| `judge` until component 5           | UNREGISTERED; refusal by absence, and the flag is read off a real emitter  | Task 32, 4/4b |
| a root plan that will not validate  | re-plan bounded by `max_replans`, then SUSPEND into approve                | Task 35, 4b   |
| a root plan whose CONDITION refutes | same ladder: another `max_replans` per granted round, else abandon         | Task 36       |
| a run that spends its NODE BUDGET   | same ladder; a grant is applied at the NEXT run start                      | Task 37       |
| the barrier's bound                 | DERIVED from the in-flight nodes' deadlines; no new knob                   | Task 34       |

Two of the four were settled by a lookup rather than a judgement call, and both lookups changed the
answer:

- The barrier needs no number because Task 21 already enforces `deadline_s` in wall clock
  (`executor_claude.py:1313`), so every in-flight node terminates on its own. What had looked like
  "this wants a measurement" was a bound that already existed one layer down.
- Item 4 as first written claimed decision 4's validator rule "already references a judge op that
  cannot run". FALSE, and corrected by reading `_requires_state_change`
  (`plan_validate.py:325-362`): the rule reads an entry's `can_change_state` flag and names no op,
  so any True-flagged entry satisfies it and a `work` node does. What misled me was Task 30's own
  test NAME, `test_root_done_when_over_only_gate_fields_is_refused_unless_judged`, which uses a
  judge merely as a convenient True-flagged entry. The real problem was narrower and is what the
  decision addresses.

Still open, and NOT a Checkpoint B item because it predates M6: `can_change_state` is a per-OP flag
while decision 4 needs a per-COMPARISON property, so `AllOf(g.rc == 0, r.count == 0)` is accepted
with zero work nodes done. Carried since Task 30; the fix belongs in `_requires_state_change`.

Then components 5 to 10, in the order the mid plan lists them. Component 5 builds the judge this
checkpoint just unregistered, and sets its `can_change_state` by reading the emitter it writes.
Component 8 carries the list Checkpoint A recorded above, which is why that block is written out
there rather than left in the probe note.

---

## Probes P1-P4: re-verify the differentiator list against the current Claude Code

Added 2026-08-30. **Why these exist:** agentdag's differentiators were established by elimination
against Claude Code's Workflow tool read at SOURCE level (binary 2.1.238) on 2026-08-20. A
documentation re-read on 2026-08-30 against CLI 2.1.251 and SDK 0.2.148 found two of the four
weakened, one already-ships entry refuted, and two surfaces that had never been assessed at all.
That entry, and two beside it, now sit in the `### Partially ships` tier: each ships, but only
under a condition the one-word list entry hid.
Findings and every quote: `RESEARCH/workflow/design/2026-08-30-claude-code-surface-re-read.md`.

**These probes exist because DOC-READ is the weaker instrument.** The user decided on 2026-08-30 to
SUSPEND the affected rows rather than correct them, precisely so that a documentation read does not
silently overturn a binary read plus a measured test. So the output of each probe is evidence at
source or measurement tier, nothing less.

**Order.** P2 first: it is the most consequential and the cheapest to be wrong about. P1 and P4
share apparatus and should run together. P3 is independent and can go any time.

**Scope discipline for all four.** Each must state what result would RESTORE the differentiator, so
a null result is readable rather than merely disappointing. Verify each hand-rolled detector against
a known NEGATIVE before believing it, and for every arm show it COULD have reported the other
answer. None of these probes may change a plan's scope text; they produce evidence, and the scope
decision that follows is the user's.

- [x] **P2: does the SDK's `defer` decision really let the process exit and resume?**
      ANSWERED 2026-08-31, `RESEARCH/workflow/design/probes/cli-surface-p2-p3.md`. Yes, and
      exactly as documented: a deferred Write ended the run with `stop_reason: tool_deferred`, the
      tool did not run, and a resume in a fresh process re-fired the SAME `tool_use` id under a
      prompt that never asked for a write. So the resume machinery does not restore the
      differentiator. GRANULARITY does: `defer` intercepts a call on its way IN, so it cannot ask
      whether what came OUT is acceptable, and gating a completed node through it would need the
      model to emit a sentinel call - branching on prose. Also refused outright for a call served
      to a cloud session. Write the row as a claim about granularity, not "cannot stop".
      The docs say a `PreToolUse` hook returning `defer` means "the process can exit and resume later
      from the persisted session". If true at source, Claude Code ships exit-and-resume on human
      input, which is most of differentiator 2. Establish the GRANULARITY, which is where agentdag
      may still differ: `defer` is documented against a TOOL CALL, while agentdag's `approve` gates a
      NODE'S OUTPUT inside a graph. Those are not the same gate even if the resume machinery is.
      RESTORES the differentiator if: defer cannot express "this unit of work needs sign-off before
      anything downstream runs", or the resume loses the graph position.

- [x] **P1: does a background session outlive the terminal, and what ends it?**
      ANSWERED 2026-08-31, `RESEARCH/workflow/design/probes/bg-session-p1-p4.md`. Yes on every
      leg, so the differentiator is NOT restored here. The supervisor is a real detached daemon: a
      session leader in its own session and process group, no controlling terminal, re-parented to
      init, hosting the worker as its own child, with the dispatching shell's pid kept only as a
      label in its argv. It is structurally out of reach of any terminal signal. Work PROGRESSED
      after the shell was gone - six heartbeat lines from six separate model turns over 94 seconds,
      five stamped after the shell died - so "keeps going" is not "queued". The supervisor does exit
      when idle, about 5 seconds after the last session is removed, but a FINISHED session still
      holds a live worker and legitimately keeps it up until removed.
      Documented: a per-user supervisor process runs background sessions, so closing the shell leaves
      work running; sessions stop on machine shutdown; the supervisor exits when idle. Confirm each
      leg from OUTSIDE the session, never from its own self-report - the process table, the run's
      output, or a file it writes. RESTORES the differentiator if: the supervisor does not survive
      the dispatching terminal, or "keeps going" turns out to mean queued rather than progressing.

- [x] **P4: what is actually redone after an interruption?**
      ANSWERED 2026-08-31, same note. The documented sentence did not reproduce on ANY route, and
      the row does not restore on the axis named below: resume is FINER-grained than agentdag's, not
      coarser, and the completed leg was never re-executed in three arms. Measured with an
      in-flight leg observed ADVANCING at the interrupt: (A) graceful `claude stop` plus resume, the
      in-flight subagent CONTINUED mid-task inside the same dispatch, 12 steps, one END; (C) crash
      with no resumer, the supervisor respawns the worker (`attempt: 2`) but the work HALTS - one
      step, no END, after 420s; (B) crash plus resume, the in-flight unit ran TWICE concurrently -
      22 steps, two ENDs, 25 distinct tool_use ids interleaved in one subagent transcript. C is what
      licenses reading B. So a DIFFERENT hazard partially restores: after a crash nothing makes
      resuming a unit exclusive or idempotent, which is the differentiator-3 side-effect concern by
      another route. Which two executors ran in B is inferred, not measured. The scope call is the
      user's.
      **HELD 2026-09-01 by the user: no differentiator row yet, pending the mechanism.** The two
      candidate mechanisms carry OPPOSITE implications - if resume itself is non-exclusive the
      hazard earns a row, and if it was the respawned worker racing the resumer it is an artifact
      of this arm and earns none - so the row is not written on an inference. `OPEN-WORK.md`
      rank 35 (reranked from 80, because it now gates a USER item) re-runs arm B with the
      respawned worker stopped first, leaving exactly one executor. The differentiator list is
      untouched until it reports.
      Documented: completed subagents return saved results; in-flight ones "start over from the
      beginning, so the tokens they used so far are spent again"; and in workflows a failure mid
      fan-out reruns agents that had already completed. This is the residue differentiator 1 now
      rests on, so measure the granularity rather than accepting the sentence. Pair it with P1.
      RESTORES the differentiator if: node-granular resume is coarser than agentdag's, or a
      completed unit can be re-executed, which for a node with a side effect is differentiator 3 too.

- [x] **P3: is the workflow token threshold really advisory?**
      ANSWERED 2026-08-31, same note. TWO things, and the doc read conflated them. The "Large
      workflow" BADGE is advisory: it fires telemetry and renders status text, and throws nothing.
      But a token ceiling DOES enforce - `WorkflowBudgetExceededError` at batch dispatch, counting
      OUTPUT tokens - whenever the session's turn budget is set; with no budget its guard returns
      immediately and the only always-on backstop is the 1000-agent cap. So the cut list moves: the
      accurate row names the mechanism AND its condition, rather than "every hard cap is on agent
      count". The scope call that follows is the user's.
      Documented: the large-workflow warning "is advisory: it doesn't pause or limit the run", and
      every hard cap is on agent count. Confirm at source in 2.1.251 that no token ceiling enforces,
      and record whether the SDK's `max_budget_usd` (dollars, covering subagent spend) is the only
      hard cap.
      **SETTLED 2026-09-01 by the user: neither claimed nor cut.** The pre-registered trigger read
      "RESTORES the cut if: any enforced token ceiling exists", which `OPEN-WORK.md` had recorded
      as "does P3 restore the cut item?" - opposite readings of the same sentence, and P3's result
      fires both. So the wording was not arbitrated; the measurement was decided on instead. It
      moved to the new `### Partially ships` tier in `build-plan-high.md`, whose rule is that a row
      must name the MECHANISM and the CONDITION. Two other entries moved with it, which is what
      justified a tier rather than a one-row exception.
