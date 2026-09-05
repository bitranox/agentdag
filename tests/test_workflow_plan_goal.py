"""Tests for the plan-goal workflow: where its plan works, and what bounds it there.

A run works in a worktree the coordinator owns unless the operator names a ``workspace``,
which is a SECOND isolation root outside the run directory. These arms drive the real
:func:`~agentdag.application.kernel.run.run_coordinator` with a fake only at the executor
port - the same shape ``test_kernel_root.py``'s end-to-end arm uses - so the workflow
program, the coordinator's containment check and the executor request are all in the loop.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from kernel_fakes import outcome, policy_path

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.executor_claude import allowed_writes
from agentdag.adapters.kernel.hooks_claude import deny_outside_write_set
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.workflows import get_workflow
from agentdag.application.workflows.plan_goal import WORKTREE, PlanGoalArgs
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.kernel_errors import KernelError
from agentdag.domain.models import NodeOutcome, RunStatus

if TYPE_CHECKING:
    from agentdag.adapters.kernel.hooks_claude import HookCallback
    from agentdag.application.kernel.ports import ExecutorRequest

REG = build_op_registry()


def one_work_plan(goal: str, node_id: str = "repair") -> str:
    """A plan a root may run: one ``work`` entry, whose completion is the whole condition."""
    return json.dumps(
        {
            "goal": goal,
            "entries": [
                {
                    "spec": {"node_id": node_id, "kind": "work", "deadline_s": 60.0},
                    "op": "work",
                    "args": {},
                    "brief": f"do {node_id}",
                    "output_contract": ["status"],
                    "acceptance": None,
                }
            ],
            "done_when": {"ref": {"entry": node_id, "field": "status"}, "op": "==", "value": "done"},
        }
    )


class RecordingPlanExecutor:
    """Writes a plan for a planner dispatch, works in the cwd for anything else, records both.

    A real double at the executor port, as production's Claude executor sits there: the arms
    below assert on the requests the coordinator BUILT, which is where the isolation roots a
    dispatch runs under are decided.
    """

    def __init__(self, raw: str) -> None:
        """Start with nothing dispatched; ``raw`` is what every planner dispatch writes."""
        self.requests: list[ExecutorRequest] = []
        self.raw = raw

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Write the plan for a planner, otherwise leave a file in the tree, and record it."""
        self.requests.append(request)
        if "You are a planner" in request.prompt:
            (request.node_dir / "plan.json").write_text(self.raw, encoding="utf-8")
        else:
            (request.cwd / "touched.txt").write_text("done\n", encoding="utf-8")
        return outcome({"sonnet": 10})

    def work_requests(self) -> list[ExecutorRequest]:
        """Only the non-planner dispatches: what the plan's own entries ran as."""
        return [request for request in self.requests if "You are a planner" not in request.prompt]


def drive(tmp_path: Path, args: PlanGoalArgs, executor: RecordingPlanExecutor) -> RunStatus:
    """Run plan-goal end to end over a fresh run directory and report how it ended."""
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "r1")
    outcome = asyncio.run(
        run_coordinator(
            run_dir=run_dir,
            journal=JsonlJournal(run_dir.journal_path, run_dir.audit_path),
            clock=UtcClock(),
            lock=FileRunLock(),
            holder=current_holder(),
            workflow=get_workflow("plan-goal"),
            args=args,
            executors={"claude": executor},
            gate_port=MakeTestGate(command=(sys.executable, "-c", "raise SystemExit(0)")),
            git=GitCli(),
            scanner=IsolationScanner(),
            policy=load_policy(policy_path()),
            sandbox=NoSandbox(),
            registry=REG,
            parallel=2,
            by="tester",
            token_id="local",
            resume_reason=None,
            notifier=NoNotifier(),
        )
    )
    return outcome.status


@pytest.mark.os_agnostic
def test_a_run_naming_no_workspace_still_works_in_the_run_s_own_worktree(tmp_path: Path) -> None:
    """The absent case is today's behaviour exactly: one root, and nothing outside it."""
    executor = RecordingPlanExecutor(one_work_plan("tidy the docs"))

    status = drive(tmp_path, PlanGoalArgs(goal="tidy the docs"), executor)

    assert status is RunStatus.DONE
    worked = executor.work_requests()
    assert worked, "the planned entry never ran"
    assert worked[0].extra_roots == ()
    assert worked[0].cwd == tmp_path / "runs" / "r1" / "wt" / WORKTREE


@pytest.mark.os_agnostic
def test_a_run_naming_a_workspace_works_there_and_the_write_hook_permits_it(tmp_path: Path) -> None:
    """The whole boundary, composed: the plan works in the workspace and its writes stand.

    The hook is built from the request the RUN produced, exactly as
    ``ClaudeExecutor._options_for`` builds it, so what is asserted is production's own
    composition of ``allowed_writes`` and ``deny_outside_write_set`` rather than a pairing
    invented here.
    """
    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    executor = RecordingPlanExecutor(one_work_plan("tidy the docs"))

    status = drive(tmp_path, PlanGoalArgs(goal="tidy the docs", workspace=workspace), executor)

    assert status is RunStatus.DONE
    assert (workspace / "touched.txt").read_text(encoding="utf-8") == "done\n"
    worked = executor.work_requests()
    assert worked, "the planned entry never ran"
    assert worked[0].cwd == workspace
    assert worked[0].extra_roots == (workspace,)

    hook = deny_outside_write_set(
        worked[0].isolation_root, allowed=allowed_writes(worked[0]), extra_roots=worked[0].extra_roots
    )
    assert _fire(hook, str(workspace / "src" / "mod.py")) is None
    assert _fire(hook, str(worked[0].node_dir / "notes.md")) is None
    assert _fire(hook, str(tmp_path / "outside.py")) == "deny"
    assert _fire(hook, str(worked[0].isolation_root.parent / "sibling.py")) == "deny"


@pytest.mark.os_agnostic
def test_the_planner_plans_inside_the_workspace_too(tmp_path: Path) -> None:
    """A planner reads its cwd, so a plan-goal run's planner has to be given the same root.

    Its reads are confined to that cwd and its own node directory, so a planner left on the
    run's worktree while the plan works elsewhere could not see the tree it is planning for.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    executor = RecordingPlanExecutor(one_work_plan("tidy the docs"))

    drive(tmp_path, PlanGoalArgs(goal="tidy the docs", workspace=workspace), executor)

    planner = executor.requests[0]
    assert planner.cwd == workspace
    assert planner.extra_roots == (workspace,)
    assert planner.read_roots == (planner.node_dir, workspace)


@pytest.mark.os_agnostic
def test_a_relative_workspace_argument_is_resolved_where_it_is_accepted() -> None:
    """``Path('./ws').parents`` is ``[Path('.')]``, so an unresolved path defeats containment.

    Resolved at the boundary, which is also what makes the value PERSISTED in ``state.json``
    absolute: a background child relaunches with the run directory as its cwd, and a relative
    workspace re-resolved there would name a different directory.
    """
    # Validated from a mapping of STRINGS, which is exactly what ``--arg KEY=VALUE`` produces
    # and hands to ``workflow.args_model.model_validate``.
    args = PlanGoalArgs.model_validate({"goal": "g", "workspace": "ws"})

    assert args.workspace is not None
    assert args.workspace.is_absolute()
    assert args.workspace == (Path.cwd() / "ws").resolve()
    assert json.loads(args.model_dump_json())["workspace"] == str(args.workspace)


@pytest.mark.os_agnostic
def test_a_user_relative_workspace_argument_is_expanded() -> None:
    """``~`` is what an operator types; nothing downstream expands it, so it is expanded here."""
    args = PlanGoalArgs.model_validate({"goal": "g", "workspace": "~/somewhere"})

    assert args.workspace == (Path.home() / "somewhere").resolve()


@pytest.mark.os_agnostic
def test_an_empty_workspace_argument_is_refused() -> None:
    """``--arg workspace=`` is a typo, and ``Path("")`` resolves to the process's own cwd.

    Refused rather than read as "no workspace": the two say different things, and silently
    turning a blank into the current directory would make the coordinator's own cwd the
    root a plan writes in.
    """
    # Matched on this refusal's OWN words: "workspace" alone also appears in the
    # extra-forbidden error the model raised before the field existed, so a looser pattern
    # would have passed against a model that cannot take a workspace at all.
    with pytest.raises(ValueError, match="must not be blank"):
        PlanGoalArgs.model_validate({"goal": "g", "workspace": ""})
    with pytest.raises(ValueError, match="must not be blank"):
        PlanGoalArgs.model_validate({"goal": "g", "workspace": "   "})


@pytest.mark.os_agnostic
def test_a_workspace_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    """A typo must not silently create a directory somewhere and work in it as if it were the project."""
    executor = RecordingPlanExecutor(one_work_plan("g"))

    with pytest.raises(KernelError, match="does not exist"):
        drive(tmp_path, PlanGoalArgs(goal="g", workspace=tmp_path / "nope"), executor)

    assert not (tmp_path / "nope").exists(), "a missing workspace is refused, never created"
    assert executor.requests == [], "nothing was dispatched"


@pytest.mark.os_agnostic
def test_a_workspace_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    """A file is not somewhere a plan can work, and every use of it downstream is a path join."""
    afile = tmp_path / "a-file"
    afile.write_text("x", encoding="utf-8")
    executor = RecordingPlanExecutor(one_work_plan("g"))

    with pytest.raises(KernelError, match="not a directory"):
        drive(tmp_path, PlanGoalArgs(goal="g", workspace=afile), executor)

    assert executor.requests == [], "nothing was dispatched"


def _fire(hook: HookCallback, file_path: str) -> str | None:
    """Run one write hook the way the SDK would, and return its permission decision."""

    async def call() -> dict[str, Any]:
        return dict(await hook({"tool_name": "Write", "tool_input": {"file_path": file_path}}, None, None))

    specific: dict[str, Any] = asyncio.run(call()).get("hookSpecificOutput") or {}
    decision: str | None = specific.get("permissionDecision")
    return decision
