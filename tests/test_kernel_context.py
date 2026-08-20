"""Tests for the coordinator context: the work primitive, the snapshot, and what a resume charges.

The journal, the run directory, the clock, the gate and git are the REAL adapters; the
executor, the tier policy and the isolation scanner are fakes at their ports, because
those are the seams a work node's behaviour is defined by.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.context import Coordinator
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.application.kernel.ports import ResolvedRow
from agentdag.domain.kernel_errors import KernelError
from agentdag.domain.models import Budget, Isolation, Kind, NodeOutcome, NodeSpec, NodeStatus, TierRole

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from agentdag.application.kernel.ports import ExecutorRequest


class OneRowPolicy:
    """A one-row tier policy: every spec resolves to the sonnet row on the claude executor."""

    version: str = "sha256:test"
    max_turns: int = 5
    deny_bash: tuple[str, ...] = ("git push",)
    tokens_per_row: Mapping[str, int] = {"sonnet": 10}

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        return ResolvedRow(alias="sonnet", executor="claude")


class RecordingExecutor:
    """An executor that records every request it is handed and returns one fixed outcome."""

    def __init__(self, outcome: NodeOutcome) -> None:
        self.outcome = outcome
        self.requests: list[ExecutorRequest] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Record ``request`` and return the fixed outcome."""
        self.requests.append(request)
        return self.outcome


class FakeScanner:
    """An isolation scanner that records which root it was asked about."""

    def __init__(self) -> None:
        self.roots: list[Path] = []

    def snapshot(self, root: Path) -> Mapping[str, str]:
        """Record ``root`` and return a fixed manifest."""
        self.roots.append(root)
        return {"wt/a/f.py": "sha256:0"}


def outcome(charged: dict[str, int]) -> NodeOutcome:
    """Build the DONE outcome the fake executor returns."""
    return NodeOutcome(
        status=NodeStatus.DONE,
        artefact_refs=["wt/a"],
        key_facts={"commit": "a" * 40},
        typed_fields=["commit"],
        charged_tokens=charged,
        executor_used="claude",
        model_used="sonnet",
        effort_used="-",
    )


def work_spec() -> NodeSpec:
    """Build the work node spec these tests dispatch."""
    return NodeSpec(
        node_id="w_migrate@1",
        kind=Kind.WORK,
        tier_role=TierRole.STANDARD,
        isolation=Isolation.WORKTREE,
        write_set=["wt/a/**"],
        deadline_s=3600,
        budget=Budget(tokens={"sonnet": 400_000}),
    )


def fresh_run_dir(tmp_path: Path) -> FsRunDir:
    """Lay out a fresh run directory with a worktree for the node to run in."""
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "r1")
    run_dir.worktree("a").mkdir(parents=True)
    return run_dir


def wire(
    run_dir: FsRunDir,
    executor: RecordingExecutor,
    scanner: FakeScanner,
    *,
    executors: Mapping[str, RecordingExecutor] | None = None,
) -> Coordinator:
    """Build a coordinator over ``run_dir``, as a relaunch would over an existing one.

    ``executors`` defaults to ``{"claude": executor}`` (what ``OneRowPolicy`` resolves
    to); a test that must exercise a misconfigured coordinator passes its own, e.g. an
    empty mapping to prove the resolved executor is not wired.
    """
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    return Coordinator(
        run_id="r1",
        workflow="t",
        args={},
        dispatcher=Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock()),
        run_dir=run_dir,
        clock=UtcClock(),
        executors={"claude": executor} if executors is None else executors,
        gate_port=MakeTestGate(),
        git=GitCli(),
        scanner=scanner,
        policy=OneRowPolicy(),
        sandbox=NoSandbox(),
        parallel=2,
    )


@pytest.mark.os_agnostic
def test_work_runs_the_resolved_row_s_executor_and_charges_what_it_reported(tmp_path: Path) -> None:
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner())

    record = asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert record.status == NodeStatus.DONE
    assert coordinator.tokens_by_row == {"sonnet": 120}
    request = executor.requests[0]
    assert request.model == "sonnet"
    assert request.max_turns == 5
    assert request.deny_bash == ("git push",)
    assert request.write_set == ("wt/a/**",)
    assert request.isolation_root == run_dir.root
    assert request.cwd == run_dir.worktree("a")
    assert request.prompt == Coordinator.DEFAULT_PROMPT
    written = (request.node_dir / "input.json").read_text(encoding="utf-8")
    assert '"cwd":"wt/a"' in written  # the run root's own location is not part of the key
    assert '"model":"sonnet"' in written


@pytest.mark.os_agnostic
def test_a_resumed_run_serves_the_record_and_still_counts_its_tokens(tmp_path: Path) -> None:
    run_dir = fresh_run_dir(tmp_path)
    first = wire(run_dir, RecordingExecutor(outcome({"sonnet": 120})), FakeScanner())
    asyncio.run(first.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    executor = RecordingExecutor(outcome({"sonnet": 999}))
    resumed = wire(run_dir, executor, FakeScanner())
    record = asyncio.run(resumed.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert executor.requests == []  # served from the journal: the executor was never called
    assert record.charged_tokens == {"sonnet": 120}
    assert resumed.tokens_by_row == {"sonnet": 120}  # a resumed run's totals include what it replayed


@pytest.mark.os_agnostic
def test_snapshot_asks_the_scanner_about_the_run_root(tmp_path: Path) -> None:
    run_dir = fresh_run_dir(tmp_path)
    scanner = FakeScanner()
    coordinator = wire(run_dir, RecordingExecutor(outcome({})), scanner)

    manifest = coordinator.snapshot()

    assert manifest == {"wt/a/f.py": "sha256:0"}
    assert scanner.roots == [run_dir.root]


@pytest.mark.os_agnostic
def test_work_refuses_the_resolved_executor_when_it_is_not_wired(tmp_path: Path) -> None:
    run_dir = fresh_run_dir(tmp_path)
    coordinator = wire(run_dir, RecordingExecutor(outcome({})), FakeScanner(), executors={})

    with pytest.raises(KernelError, match="not wired"):
        asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    # A misconfiguration raises before anything is dispatched: no started line, no
    # result line - a retry after fixing the wiring is not replaying a half-attempt.
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    assert journal.lines() == []


@pytest.mark.os_agnostic
def test_work_refuses_a_cwd_outside_the_run_root(tmp_path: Path) -> None:
    run_dir = fresh_run_dir(tmp_path)
    coordinator = wire(run_dir, RecordingExecutor(outcome({})), FakeScanner())
    outside = run_dir.root.parent / "elsewhere"
    outside.mkdir()

    with pytest.raises(KernelError, match="outside the run root"):
        asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=outside))

    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    assert journal.lines() == []
