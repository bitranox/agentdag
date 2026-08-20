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
from agentdag.domain.models import Budget, ErrorType, Isolation, Kind, NodeOutcome, NodeSpec, NodeStatus, TierRole

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from agentdag.application.kernel.ports import ExecutorRequest


class OneRowPolicy:
    """A one-row tier policy: every spec resolves to the sonnet row on the claude executor.

    ``tokens_per_row`` is set far above anything these tests charge or cap - it is the
    ceiling the run-level budget check (:meth:`~agentdag.application.kernel.context.Coordinator._run_cap_refusal`)
    reads, and nothing here is exercising THAT (see :class:`LowCeilingPolicy` for a
    policy that is). Kept generous rather than removed so a test added later that adds
    a couple more work dispatches over this same policy does not spuriously trip it.
    """

    version: str = "sha256:test"
    max_turns: int = 5
    deny_bash: tuple[str, ...] = ("git push",)
    tokens_per_row: Mapping[str, int] = {"sonnet": 1_000_000_000}
    deadline_ceiling_s: float = 999_999.0
    """Generous like ``tokens_per_row`` above - :class:`LowDeadlineCeilingPolicy` is the
    one that exercises the clamp; nothing here should trip it by accident."""

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        return ResolvedRow(alias="sonnet", executor="claude")


class LowCeilingPolicy(OneRowPolicy):
    """:class:`OneRowPolicy`, but a run ceiling low enough for the budget-cap tests to hit."""

    tokens_per_row: Mapping[str, int] = {"sonnet": 100}


class LowDeadlineCeilingPolicy(OneRowPolicy):
    """:class:`OneRowPolicy`, but a deadline ceiling below ``work_spec``'s own ``deadline_s``.

    ``work_spec()`` declares ``deadline_s=3600``; this ceiling is well under that, so a
    dispatch under this policy proves :meth:`~agentdag.application.kernel.context.Coordinator.work`
    clamps the SPEC's requested deadline to ``policy.deadline_ceiling_s`` before it ever
    reaches :class:`~agentdag.application.kernel.ports.ExecutorRequest`.
    """

    deadline_ceiling_s: float = 30.0


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
    policy: OneRowPolicy | None = None,
) -> Coordinator:
    """Build a coordinator over ``run_dir``, as a relaunch would over an existing one.

    ``executors`` defaults to ``{"claude": executor}`` (what ``OneRowPolicy`` resolves
    to); a test that must exercise a misconfigured coordinator passes its own, e.g. an
    empty mapping to prove the resolved executor is not wired. ``policy`` defaults to
    ``OneRowPolicy()``; a budget-cap test passes ``LowCeilingPolicy()`` instead.
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
        policy=OneRowPolicy() if policy is None else policy,
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
    """An available row naming an unwired executor is a FAILED record, not an
    exception escaping the coordinator (design: "the design promises a typed
    refusal and a record" - see :meth:`Coordinator.work`'s own docstring for why
    this is checked inside ``body``, unlike the cwd check below it, and what that
    trades away). One misresolved node must not abort the whole run: the record
    names the row alias, the unwired executor key, and what IS wired, so the
    operator can tell what is wrong without a stack trace.
    """
    run_dir = fresh_run_dir(tmp_path)
    coordinator = wire(run_dir, RecordingExecutor(outcome({})), FakeScanner(), executors={})

    record = asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert record.status == NodeStatus.FAILED
    assert record.error is not None
    assert record.error.type == ErrorType.EXECUTOR_ERROR
    assert record.error.transient is False
    assert "sonnet" in record.error.message  # the resolved row's own alias
    assert "claude" in record.error.message  # the executor key that is not wired
    assert "not wired" in record.error.message

    # Unlike the cwd misconfiguration below, this one IS recorded: a started line and
    # a result line, same as any other node the body raised inside.
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    assert len(journal.lines()) == 2


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


@pytest.mark.os_agnostic
def test_work_threads_the_node_s_own_cap_into_the_executor_request(tmp_path: Path) -> None:
    """The other half of the token cap's two call sites (design 7): the per-node,
    per-turn check lives in the executor, which needs the cap on the request it is
    handed - this is what ``work()`` sends it.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner())

    asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert executor.requests[0].token_cap == 400_000  # work_spec()'s own Budget(tokens={"sonnet": 400_000})


@pytest.mark.os_agnostic
def test_work_threads_the_node_s_own_deadline_into_the_executor_request(tmp_path: Path) -> None:
    """The node deadline's own call site (design 7, M3): a DIFFERENT quantity from the
    token cap above (wall-clock seconds, never a token count) and a DIFFERENT limits
    field (``deadline_ceiling_s``, never ``tokens_per_row``) - proven here under a
    ceiling generous enough that ``work_spec()``'s own ``deadline_s=3600`` reaches the
    executor UNCHANGED.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner())

    asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert executor.requests[0].deadline_s == 3600  # work_spec()'s own deadline_s, well under OneRowPolicy's ceiling


@pytest.mark.os_agnostic
def test_work_clamps_the_node_s_deadline_to_the_policy_s_ceiling(tmp_path: Path) -> None:
    """The clamp itself (design 2.3 rule 4): ``work_spec()`` declares ``deadline_s=3600``,
    well past :class:`LowDeadlineCeilingPolicy`'s own 30-second ceiling - the SPEC's own
    value must never reach the executor unclamped, whatever a node asked for.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner(), policy=LowDeadlineCeilingPolicy())

    asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert executor.requests[0].deadline_s == 30.0  # the ceiling, not the spec's own 3600


@pytest.mark.os_agnostic
def test_work_is_not_capped_when_the_spec_declares_no_budget_for_the_resolved_row(tmp_path: Path) -> None:
    """A node with no declared cap for the resolved row is checked against NEITHER
    call site - not the per-turn one (``token_cap`` stays ``None``) and not the
    run-level one (:meth:`~agentdag.application.kernel.context.Coordinator._run_cap_refusal`
    returns ``None`` on a ``None`` cap before it ever reads the ceiling), even under a
    ceiling (``LowCeilingPolicy``) low enough to refuse ``work_spec()``'s own cap.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 5}))
    coordinator = wire(run_dir, executor, FakeScanner(), policy=LowCeilingPolicy())
    spec = work_spec().model_copy(update={"budget": Budget()})

    record = asyncio.run(coordinator.work(spec, brief="migrate", cwd=run_dir.worktree("a")))

    assert record.status == NodeStatus.DONE
    assert executor.requests[0].token_cap is None


@pytest.mark.os_agnostic
def test_work_refuses_the_dispatch_when_the_node_s_own_cap_would_push_the_run_past_its_ceiling(
    tmp_path: Path,
) -> None:
    """The run-level cap (design 7): the SECOND call site, evaluated before the
    executor is ever called. ``work_spec()``'s node declares a 400,000-token cap on
    ``sonnet``; :class:`LowCeilingPolicy` caps the whole run at 100 - so this dispatch
    must be refused OUTRIGHT, never reach the executor, and still produce a normal
    ``started``/``result`` journal pair (the always-a-record invariant: a refusal is a
    RECORD, not an exception).
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner(), policy=LowCeilingPolicy())

    record = asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert record.status == NodeStatus.FAILED
    assert record.error is not None
    assert record.error.type == "budget_exceeded"
    assert record.error.transient is False
    assert executor.requests == []  # never dispatched
    assert coordinator.tokens_by_row == {}  # nothing was actually spent
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    # Still a normal started/result pair, like any other dispatch - the refusal is
    # JOURNALED, not a silent skip: a resume of this run must see it, not re-attempt it.
    assert [type(line).__name__ for line in journal.lines()] == ["StartedLine", "ResultLine"]


@pytest.mark.os_agnostic
def test_work_dispatches_normally_when_the_node_s_cap_fits_under_the_run_ceiling(tmp_path: Path) -> None:
    """Control for the refusal test above: the identical node spec, under a ceiling
    its own cap fits comfortably under, runs the executor as normal.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner())  # OneRowPolicy's generous ceiling

    record = asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert record.status == NodeStatus.DONE
    assert executor.requests[0].token_cap == 400_000
    assert coordinator.tokens_by_row == {"sonnet": 120}
