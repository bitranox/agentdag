"""Tests for the coordinator context: the work primitive, the snapshot, and what a resume charges.

The journal, the run directory, the clock, the gate and git are the REAL adapters; the
executor, the tier policy and the isolation scanner are fakes at their ports, because
those are the seams a work node's behaviour is defined by.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from jsonschema.exceptions import ValidationError
from schema_helpers import validator

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.context import Coordinator
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.application.kernel.ports import ResolvedRow, stamp
from agentdag.domain.handover import HANDOVER_AS_WRITTEN_FILENAME, HANDOVER_FILENAME, IDENTITY_KEYS
from agentdag.domain.journal import ResultLine, RetryGrantLine, StartedLine
from agentdag.domain.kernel_errors import KernelError, Suspended
from agentdag.domain.models import (
    Budget,
    ErrorType,
    Isolation,
    Kind,
    NodeError,
    NodeOutcome,
    NodeSpec,
    NodeStatus,
    SuspendReason,
    TierRole,
)
from agentdag.domain.plan import PLAN_FILENAME
from agentdag.domain.policy import FailureAction

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from agentdag.application.graph_a_ports import GatePort
    from agentdag.application.kernel.ports import Executor, ExecutorRequest


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
    max_attempts: int = 1
    max_continuations: int = 3
    deny_bash: tuple[str, ...] = ("git push",)
    on_auth_failure: FailureAction = FailureAction.FAIL_RUN
    on_rate_limit: FailureAction = FailureAction.SUSPEND_RUN
    tokens_per_row: Mapping[str, int] = {"sonnet": 1_000_000_000}
    deadline_ceiling_s: float = 999_999.0
    """Generous like ``tokens_per_row`` above - :class:`LowDeadlineCeilingPolicy` is the
    one that exercises the clamp; nothing here should trip it by accident."""

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        return ResolvedRow(alias="sonnet", executor="claude", handover_at_tokens=100_000)


class RetryingPolicy(OneRowPolicy):
    """:class:`OneRowPolicy`, but a run that gives a transiently-failing node three tries."""

    max_attempts: int = 3


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
    executor: Executor,
    scanner: FakeScanner,
    *,
    executors: Mapping[str, Executor] | None = None,
    policy: OneRowPolicy | None = None,
    gate_port: GatePort | None = None,
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
        gate_port=MakeTestGate() if gate_port is None else gate_port,
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
    # The task is no longer the WHOLE prompt: every dispatched node carries the standing
    # stop duty ahead of it (decision 14). Containment, not equality - the duty's own
    # content is asserted by test_work_gives_the_node_the_standing_stop_duty.
    assert Coordinator.DEFAULT_PROMPT in request.prompt
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


class FlakyGate:
    """A gate port that raises for its first ``fails`` calls, then reports a green gate."""

    def __init__(self, *, fails: int, raising: type[Exception] = OSError) -> None:
        self.fails = fails
        self.raising = raising
        self.calls = 0

    def run(self, worktree: Path, log: Path) -> int:
        """Count the call, raise while the failure budget lasts, else report success."""
        del worktree, log
        self.calls += 1
        if self.calls <= self.fails:
            raise self.raising("the gate runner fell over")
        return 0


class RedGate:
    """A gate port that runs to completion and reports a real, non-zero answer."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, worktree: Path, log: Path) -> int:
        """Count the call and report a red gate."""
        del worktree, log
        self.calls += 1
        return 1


def gate_spec() -> NodeSpec:
    """Build the gate node spec the retry tests dispatch."""
    return NodeSpec(
        node_id="g_test@1",
        kind=Kind.GATE,
        executor="code",
        isolation=Isolation.NONE,
        deadline_s=60,
        budget=Budget(),
    )


def gated(run_dir: FsRunDir, gate_port: FlakyGate | RedGate, *, policy: OneRowPolicy | None = None) -> Coordinator:
    """Wire a coordinator whose gate port is the test's own, everything else as ``wire`` builds it."""
    return wire(run_dir, RecordingExecutor(outcome({})), FakeScanner(), policy=policy, gate_port=gate_port)


@pytest.mark.os_agnostic
def test_a_transient_code_failure_is_retried_and_the_second_attempt_stands(tmp_path: Path) -> None:
    """A gate runner that falls over is infrastructure, not an answer, so the node runs again."""
    run_dir = fresh_run_dir(tmp_path)
    gate = FlakyGate(fails=1)
    coordinator = gated(run_dir, gate, policy=RetryingPolicy())

    record = asyncio.run(coordinator.gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 2
    assert record.status == NodeStatus.DONE
    assert record.attempt == 1


@pytest.mark.os_agnostic
def test_a_red_gate_is_never_retried(tmp_path: Path) -> None:
    """A red gate ran to completion and reported a real answer; re-running it would loop on it."""
    run_dir = fresh_run_dir(tmp_path)
    gate = RedGate()
    coordinator = gated(run_dir, gate, policy=RetryingPolicy())

    record = asyncio.run(coordinator.gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 1
    assert record.status == NodeStatus.FAILED
    assert record.error is None


@pytest.mark.os_agnostic
def test_a_config_bug_is_never_retried(tmp_path: Path) -> None:
    """A KernelError is stamped non-transient: the same inputs reproduce it, so a retry burns budget."""
    run_dir = fresh_run_dir(tmp_path)
    gate = FlakyGate(fails=9, raising=KernelError)
    coordinator = gated(run_dir, gate, policy=RetryingPolicy())

    record = asyncio.run(coordinator.gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 1
    assert record.error is not None
    assert record.error.transient is False


@pytest.mark.os_agnostic
def test_the_attempt_cap_bounds_a_failure_that_never_clears(tmp_path: Path) -> None:
    run_dir = fresh_run_dir(tmp_path)
    gate = FlakyGate(fails=9)
    coordinator = gated(run_dir, gate, policy=RetryingPolicy())

    record = asyncio.run(coordinator.gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 3  # RetryingPolicy.max_attempts
    assert record.status == NodeStatus.FAILED
    assert record.attempt == 2


@pytest.mark.os_agnostic
def test_a_policy_allowing_one_attempt_retries_nothing(tmp_path: Path) -> None:
    """The knob's floor is the shipped behaviour before it existed, so a run can opt out."""
    run_dir = fresh_run_dir(tmp_path)
    gate = FlakyGate(fails=1)
    coordinator = gated(run_dir, gate)

    record = asyncio.run(coordinator.gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 1
    assert record.status == NodeStatus.FAILED


@pytest.mark.os_agnostic
def test_every_attempt_is_journaled_under_its_own_key(tmp_path: Path) -> None:
    """attempt is an identity field, so a retry is a new call and the failure is never overwritten."""
    run_dir = fresh_run_dir(tmp_path)
    coordinator = gated(run_dir, FlakyGate(fails=1), policy=RetryingPolicy())

    asyncio.run(coordinator.gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    lines = JsonlJournal(run_dir.journal_path, run_dir.audit_path).lines()
    results = [line for line in lines if isinstance(line, ResultLine)]
    assert [line.record.attempt for line in results] == [0, 1]
    assert len({line.key for line in results}) == 2


@pytest.mark.os_agnostic
def test_a_work_node_is_not_retried_in_place(tmp_path: Path) -> None:
    """Design 2.3 rule 5 owns a model node's retry, and it escalates a rank rather than repeating."""
    run_dir = fresh_run_dir(tmp_path)
    failed = NodeOutcome(
        status=NodeStatus.FAILED,
        executor_used="claude",
        model_used="sonnet",
        effort_used="-",
        error=NodeError(type=ErrorType.EXECUTOR_ERROR, message="the model call fell over", transient=True),
    )
    executor = RecordingExecutor(failed)
    coordinator = wire(run_dir, executor, FakeScanner(), policy=RetryingPolicy())

    record = asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert len(executor.requests) == 1
    assert record.status == NodeStatus.FAILED


def grant_last_failure(run_dir: FsRunDir, *, node_id: str) -> str:
    """Append a retry grant for the journal's LATEST failed result, as ``run retry`` does.

    Returns:
        The granted key, so a test can assert the attempt that follows is a different one.
    """
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    failed = [
        line for line in journal.lines() if isinstance(line, ResultLine) and line.record.status is NodeStatus.FAILED
    ]
    key = failed[-1].key
    journal.append(
        RetryGrantLine(
            node_id=node_id, key=key, reason="fixed the repo by hand", by="me", token_id="local", at=stamp(UtcClock())
        )
    )
    return key


@pytest.mark.os_agnostic
def test_a_red_gate_runs_again_when_a_person_grants_it(tmp_path: Path) -> None:
    """The case the automatic rule cannot reach: a red gate ran and reported a real answer,
    so nothing retries it - but a person who fixed the repo by hand changed something no
    journal key can see, and the failure is otherwise served back for ever."""
    run_dir = fresh_run_dir(tmp_path)
    gate = RedGate()
    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))
    assert gate.calls == 1

    granted = grant_last_failure(run_dir, node_id="g_test@1")
    record = asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 2
    assert record.attempt == 1
    assert record.input_hash != granted  # attempt is an identity field, so this is a NEW key


@pytest.mark.os_agnostic
def test_a_relaunch_without_a_grant_serves_the_failure_back(tmp_path: Path) -> None:
    """The control the test above needs: without the grant, the second launch runs nothing."""
    run_dir = fresh_run_dir(tmp_path)
    gate = RedGate()
    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))
    record = asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 1
    assert record.attempt == 0


@pytest.mark.os_agnostic
def test_a_grant_is_spent_by_the_attempt_it_authorises(tmp_path: Path) -> None:
    """Self-limiting by construction: the granted attempt has a DIFFERENT key, so the grant
    cannot match a second time and an unattended run can never loop on it."""
    run_dir = fresh_run_dir(tmp_path)
    gate = RedGate()
    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))
    grant_last_failure(run_dir, node_id="g_test@1")
    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))
    assert gate.calls == 2

    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 2


@pytest.mark.os_agnostic
def test_a_grant_naming_a_key_whose_record_passed_changes_nothing(tmp_path: Path) -> None:
    """The scope guard is the RECORD: a grant can only ever buy an attempt for a failure."""
    run_dir = fresh_run_dir(tmp_path)
    gate = FlakyGate(fails=0)
    record = asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))
    assert record.status == NodeStatus.DONE

    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    journal.append(
        RetryGrantLine(
            node_id="g_test@1", key=record.input_hash, reason="", by="me", token_id="local", at=stamp(UtcClock())
        )
    )
    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == 1


@pytest.mark.os_agnostic
def test_a_grant_reaches_only_the_node_it_names(tmp_path: Path) -> None:
    """A journal key carries no node id, so two nodes whose work is identical share one. The
    grant must still buy ONE attempt: matching the key alone would run the granted attempt once
    per twin - N model dispatches and N charges from one grant, and two bodies in one worktree.
    """
    run_dir = fresh_run_dir(tmp_path)
    gate = RedGate()
    twin = gate_spec().model_copy(update={"node_id": "g_test@2"})
    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))
    asyncio.run(gated(run_dir, gate).gate(twin, argv=["make", "test"], cwd=run_dir.root))
    grant_last_failure(run_dir, node_id="g_test@1")
    calls_before = gate.calls

    coordinator = gated(run_dir, gate)
    asyncio.run(coordinator.gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))
    asyncio.run(coordinator.gate(twin, argv=["make", "test"], cwd=run_dir.root))

    assert gate.calls == calls_before + 1


@pytest.mark.os_agnostic
def test_a_replay_after_a_granted_attempt_dispatches_nothing_new(tmp_path: Path) -> None:
    """The folded grant stays in the journal for ever, so a later replay re-makes the same
    decision in the same order and the key sequence still matches the journal's own."""
    run_dir = fresh_run_dir(tmp_path)
    gate = RedGate()
    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))
    grant_last_failure(run_dir, node_id="g_test@1")
    asyncio.run(gated(run_dir, gate).gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    replay = gated(run_dir, gate)
    asyncio.run(replay.gate(gate_spec(), argv=["make", "test"], cwd=run_dir.root))

    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    started = [line.key for line in journal.lines() if isinstance(line, StartedLine)]
    assert replay.dispatcher.dispatched_keys == started
    assert gate.calls == 2


@pytest.mark.os_agnostic
def test_work_threads_the_row_s_context_ceiling_into_the_executor_request(tmp_path: Path) -> None:
    """The context ceiling's own call site (design 3.8): a THIRD quantity, from a THIRD source.

    The token cap comes from the SPEC's own budget and the deadline from the spec clamped
    by a run limit; this comes from the resolved MODEL ROW, because how full a window is
    depends on which window it is. A node cannot declare it and a run limit cannot clamp
    it - the row owns it, so it reaches the executor exactly as the policy resolved it.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner())

    asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert executor.requests[0].handover_at_tokens == 100_000  # OneRowPolicy's sonnet row


class ContinuingExecutor:
    """Ends ``needs_continuation`` for its first ``hands_over`` dispatches, then done.

    The executor-side half of design 3.8 is already built: a node past its row's context
    ceiling comes back ``NEEDS_CONTINUATION`` with its artefact ref intact. This fake
    stands in for that so the COORDINATOR half can be driven without a model.
    """

    def __init__(self, hands_over: int) -> None:
        self.hands_over = hands_over
        self.requests: list[ExecutorRequest] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Hand over until the quota is spent, then finish."""
        self.requests.append(request)
        if len(self.requests) <= self.hands_over:
            return NodeOutcome(
                status=NodeStatus.NEEDS_CONTINUATION,
                artefact_refs=["wt/a"],
                key_facts={"context_at_handover": 120_000},
                typed_fields=["context_at_handover"],
                charged_tokens={"sonnet": 120},
                executor_used="claude",
                model_used="sonnet",
                effort_used="-",
                error=None,
            )
        return outcome({"sonnet": 120})


@pytest.mark.os_agnostic
def test_a_node_that_hands_over_is_continued_by_a_successor(tmp_path: Path) -> None:
    """The work is CONTINUED, never discarded (design 3.8).

    A node that ends ``needs_continuation`` earns a successor dispatched with
    ``continuation + 1`` - a different journal key, so a genuine re-run rather than the
    old record served back, exactly as ``attempt + 1`` works for a retry. The chain's
    last record is what the caller sees.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = ContinuingExecutor(hands_over=1)
    coordinator = wire(run_dir, executor, FakeScanner())

    record = asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert len(executor.requests) == 2  # the node, then its successor
    assert record.status == NodeStatus.DONE
    assert record.continuation == 1  # the successor's own counter, next to attempt in the key


class ShortChainPolicy(OneRowPolicy):
    """:class:`OneRowPolicy`, but a chain that may take only two handovers."""

    max_continuations: int = 2


@pytest.mark.os_agnostic
def test_a_chain_past_max_continuations_ends_failed_rather_than_handing_over_forever(tmp_path: Path) -> None:
    """A node that hands over every time must not continue without end (design 3.8).

    ``max_continuations`` is the ONLY thing that bounds a chain: a context ceiling is not
    a failure, so no retry rule and no budget refusal is reached by simply handing over.
    The bound is enforced where the successor is DISPATCHED, so the refusal gets a record
    and a journal line of its own - a run can then say why the chain stopped instead of
    merely having no more records.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = ContinuingExecutor(hands_over=99)  # would hand over for ever if nothing stopped it
    coordinator = wire(run_dir, executor, FakeScanner(), policy=ShortChainPolicy())

    record = asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert len(executor.requests) == 3  # continuations 0, 1 and 2 ran; the fourth never reached it
    assert record.status == NodeStatus.FAILED
    assert record.error is not None
    assert record.error.type == "continuation_limit"
    assert record.error.transient is False  # more tries cannot help; the chain is out of links
    assert record.continuation == 3


@pytest.mark.os_agnostic
def test_work_gives_the_node_the_standing_stop_duty(tmp_path: Path) -> None:
    """A dispatched work node carries the handover duty in its own prompt (decision 14).

    The duty has to be present from DISPATCH: measured over 40 dispatches
    (RESEARCH ``workflow/design/probes/handover-nudge-inject.md``), a stop notice arriving
    with no prior standing in the task is refused 4 times out of 4 as prompt injection,
    while the same notice against a brief that pre-authorises it is obeyed 4 of 4.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner())

    asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert len(executor.requests) == 1
    prompt = executor.requests[0].prompt
    assert "authoritative" in prompt.lower()
    assert "Apply the change described in your system prompt" in prompt  # the task survives


@pytest.mark.os_agnostic
def test_work_names_an_absolute_handover_path_in_the_duty(tmp_path: Path) -> None:
    """The duty names the node's own artefact dir, absolutely.

    Absolute because the probe measured a node resolving a bare filename against the wrong
    directory; and inside ``node_dir`` because that is the one place besides its declared
    write set the node is already permitted to write (``allowed_writes``), so the handover
    cannot be denied by the write-set hook.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(outcome({"sonnet": 120}))
    coordinator = wire(run_dir, executor, FakeScanner())

    asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    request = executor.requests[0]
    expected = str(request.node_dir / HANDOVER_FILENAME)
    assert expected in request.prompt
    assert request.node_dir.is_absolute()


class HandoverExecutor:
    """An executor that writes a node-authored handover record, then hands over.

    The record holds exactly what the DUTY asks for and none of the identity keys, because
    that is what a compliant node writes: measured across every probe dispatch to date, 69 of
    69 duty-shaped records failed the full schema on the identity keys and nothing else.
    """

    def __init__(self, *, writes_record: bool = True, raw: str | None = None) -> None:
        self.requests: list[ExecutorRequest] = []
        self.writes_record = writes_record
        self.raw = raw
        self.written_raw = ""

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Record ``request``, write the handover if this executor writes one, and hand over."""
        self.requests.append(request)
        if self.writes_record:
            body = (
                self.raw
                if self.raw is not None
                else json.dumps(
                    {
                        "done": ["01: read and counted"],
                        "left": ["02: write the count"],
                        "key_facts": {"count": 7},
                        "artefact_refs": ["wt/a"],
                        "write_set_state": "dirty",
                        "next_step": "write outbox/02.txt",
                    }
                )
            )
            self.written_raw = body
            (request.node_dir / HANDOVER_FILENAME).write_text(body, encoding="utf-8")
        return NodeOutcome(
            status=NodeStatus.NEEDS_CONTINUATION,
            artefact_refs=["wt/a"],
            key_facts={},
            typed_fields=[],
            charged_tokens={"sonnet": 10},
            executor_used="claude",
            model_used="sonnet",
            effort_used="-",
        )


def _stamped_records(executor: HandoverExecutor) -> list[dict[str, object]]:
    """Read back every handover record the run left behind, in dispatch order."""
    return [
        json.loads((request.node_dir / HANDOVER_FILENAME).read_text(encoding="utf-8")) for request in executor.requests
    ]


@pytest.mark.os_agnostic
def test_a_handover_record_is_stamped_with_the_link_that_wrote_it(tmp_path: Path) -> None:
    """Decision 16: the coordinator adds the identity keys, using the CURRENT spec.

    The continuations are the whole point. A body closes over ``work()``'s ORIGINAL spec, so a
    stamp taken from there would read 0 on every link of the chain and look perfectly fine.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = HandoverExecutor()
    coordinator = wire(run_dir, executor, FakeScanner())

    asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    records = _stamped_records(executor)
    assert [r["continuation"] for r in records] == [0, 1, 2, 3]
    assert {r["node_id"] for r in records} == {"w_migrate@1"}
    assert {r["attempt"] for r in records} == {0}
    assert records[0]["next_step"] == "write outbox/02.txt"  # the node's own content survives


@pytest.mark.os_agnostic
def test_the_node_s_own_bytes_survive_the_stamp(tmp_path: Path) -> None:
    """Stamping rewrites handover.json, so what the node actually wrote is kept beside it.

    The evidence every faithfulness question is answered from is the node's WORDING, and a
    reformatted, re-ordered rewrite is not it.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = HandoverExecutor()

    asyncio.run(wire(run_dir, executor, FakeScanner()).work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    node_dir = executor.requests[0].node_dir
    as_written = (node_dir / HANDOVER_AS_WRITTEN_FILENAME).read_text(encoding="utf-8")
    assert as_written == executor.written_raw  # byte for byte, not merely equivalent JSON
    assert set(IDENTITY_KEYS).isdisjoint(json.loads(as_written))  # untouched by the coordinator
    assert set(IDENTITY_KEYS) <= set(json.loads((node_dir / HANDOVER_FILENAME).read_text(encoding="utf-8")))


@pytest.mark.os_agnostic
def test_nothing_is_preserved_when_nothing_is_overwritten(tmp_path: Path) -> None:
    """An unparseable record is left alone, so there is no rewrite to preserve it from."""
    run_dir = fresh_run_dir(tmp_path)
    garbage = HandoverExecutor(raw="{not json at all")

    asyncio.run(wire(run_dir, garbage, FakeScanner()).work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    node_dir = garbage.requests[0].node_dir
    assert (node_dir / HANDOVER_FILENAME).read_text(encoding="utf-8") == "{not json at all"
    assert not (node_dir / HANDOVER_AS_WRITTEN_FILENAME).exists()


@pytest.mark.os_agnostic
def test_a_stamped_handover_record_validates_against_the_shipped_schema(tmp_path: Path) -> None:
    """The point of the stamp: a duty-shaped record becomes schema-valid, with nothing asked of the node."""
    run_dir = fresh_run_dir(tmp_path)
    executor = HandoverExecutor()

    asyncio.run(wire(run_dir, executor, FakeScanner()).work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    record = _stamped_records(executor)[0]
    validator("handover").validate(record)
    # RED control: strip the coordinator's half back off and the same record must fail again,
    # so this test cannot pass by validating something the schema never constrained.
    node_authored = {k: v for k, v in record.items() if k not in set(IDENTITY_KEYS)}
    with pytest.raises(ValidationError):
        validator("handover").validate(node_authored)


@pytest.mark.os_agnostic
def test_a_replayed_handover_keeps_the_identity_it_was_written_with(tmp_path: Path) -> None:
    """A replay must not re-stamp: the record says what happened, not what was asked last."""
    run_dir = fresh_run_dir(tmp_path)
    first = HandoverExecutor()
    asyncio.run(wire(run_dir, first, FakeScanner()).work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    path = first.requests[0].node_dir / HANDOVER_FILENAME
    path.write_text(json.dumps({"node_id": "from-the-first-run", "attempt": 7, "continuation": 9}), encoding="utf-8")

    second = HandoverExecutor()
    asyncio.run(wire(run_dir, second, FakeScanner()).work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert second.requests == []  # served from the journal; no body ran, so nothing stamped
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "node_id": "from-the-first-run",
        "attempt": 7,
        "continuation": 9,
    }


@pytest.mark.os_agnostic
def test_a_handover_with_no_record_or_an_unreadable_one_is_left_exactly_as_found(tmp_path: Path) -> None:
    """Neither absence nor garbage is the coordinator's to repair - both are the node's report."""
    run_dir = fresh_run_dir(tmp_path)
    silent = HandoverExecutor(writes_record=False)
    asyncio.run(wire(run_dir, silent, FakeScanner()).work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))
    assert not (silent.requests[0].node_dir / HANDOVER_FILENAME).exists()

    other = fresh_run_dir(tmp_path / "second")
    garbage = HandoverExecutor(raw="{not json at all")
    asyncio.run(wire(other, garbage, FakeScanner()).work(work_spec(), brief="migrate", cwd=other.worktree("a")))
    assert (garbage.requests[0].node_dir / HANDOVER_FILENAME).read_text(encoding="utf-8") == "{not json at all"


def rate_limited_outcome() -> NodeOutcome:
    """Build the outcome the executor reports when the provider refused for quota."""
    return NodeOutcome(
        status=NodeStatus.FAILED,
        executor_used="claude",
        model_used="sonnet",
        effort_used="-",
        error=NodeError(type=ErrorType.RATE_LIMITED, message="rate limited", transient=False),
    )


class FailOnRateLimitPolicy(OneRowPolicy):
    """A policy whose operator chose to lose the run rather than leave it resumable."""

    on_rate_limit: FailureAction = FailureAction.FAIL_RUN


@pytest.mark.os_agnostic
def test_a_rate_limited_work_node_suspends_the_run_and_records_nothing(tmp_path: Path) -> None:
    """Quota clears on its own, so the run must end resumable with the node un-recorded.

    Recording it would defeat the resume: every result is served on replay whatever its
    status, so a recorded rate-limit failure is the outcome the resumed run would read
    back instead of re-dispatching. The absent ``result`` line is the whole mechanism.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(rate_limited_outcome())
    coordinator = wire(run_dir, executor, FakeScanner())

    with pytest.raises(Suspended) as caught:
        asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert caught.value.reason is SuspendReason.QUOTA
    assert caught.value.node_id == "w_migrate@1"
    assert caught.value.payload_hash is None  # a quota suspend asks nobody anything
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    assert [type(line).__name__ for line in journal.lines()] == ["StartedLine"]


@pytest.mark.os_agnostic
def test_a_rate_limited_work_node_fails_the_run_when_the_policy_says_so(tmp_path: Path) -> None:
    """The knob is the thing under test: the same executor outcome, the other answer.

    Without this arm ``on_rate_limit`` would be satisfied by the shipped value alone and
    nothing would prove the code READS it rather than hard-coding what it happens to say.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = RecordingExecutor(rate_limited_outcome())
    coordinator = wire(run_dir, executor, FakeScanner(), policy=FailOnRateLimitPolicy())

    record = asyncio.run(coordinator.work(work_spec(), brief="migrate", cwd=run_dir.worktree("a")))

    assert record.status == NodeStatus.FAILED
    assert record.error is not None
    assert record.error.type == ErrorType.RATE_LIMITED


class PlanWritingExecutor:
    """An executor that writes ``plan.json`` into the node dir, as a planner node does.

    A real double rather than a patch of the read: the seam under test is "a node ran and
    left a file behind in its own dispatch directory", so the executor is where the file
    has to come from.
    """

    def __init__(self, raw: str | None) -> None:
        self.raw = raw
        self.requests: list[ExecutorRequest] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Write the plan (when this double has one) and return a DONE outcome."""
        self.requests.append(request)
        if self.raw is not None:
            (request.node_dir / PLAN_FILENAME).write_text(self.raw, encoding="utf-8")
        return outcome({"sonnet": 10})


@pytest.mark.os_agnostic
def test_plan_node_surfaces_the_nodes_plan_json_as_an_artefact_ref(tmp_path: Path) -> None:
    """The planner's output has to reach the coordinator, and node_dir never escapes body().

    `work()` returns the executor's outcome, whose artefact_refs hold the node's CWD, and a
    node dir is not derivable from a record (journal_key needs brief_hash and prefix, and a
    ResultRecord carries neither). So a planner node needs its own primitive that reads the
    file back, the way gate() already surfaces gate.log.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = PlanWritingExecutor('{"goal": "g"}')
    coordinator = wire(run_dir, executor, FakeScanner())

    record = asyncio.run(coordinator.plan_node(work_spec(), brief="plan it", cwd=run_dir.worktree("a")))

    rel = next(r for r in record.artefact_refs if r.endswith(PLAN_FILENAME))
    assert run_dir.read_text(rel) == '{"goal": "g"}'


@pytest.mark.os_agnostic
def test_plan_node_surfaces_no_ref_when_the_node_wrote_no_plan(tmp_path: Path) -> None:
    """The control. A planner that wrote nothing must be distinguishable from one that did,
    or the caller cannot tell "no plan" from "a plan I failed to find"."""
    run_dir = fresh_run_dir(tmp_path)
    coordinator = wire(run_dir, PlanWritingExecutor(None), FakeScanner())

    record = asyncio.run(coordinator.plan_node(work_spec(), brief="plan it", cwd=run_dir.worktree("a")))

    assert not [r for r in record.artefact_refs if r.endswith(PLAN_FILENAME)]
