"""RED/GREEN tests that INVOKE what the op registry builds, over a real coordinator.

``validate_plan`` never calls :attr:`~agentdag.application.kernel.registry.OpSpec.build`, so
Task 30's own suite never ran a single registered body - which is why ``reduce:count``
counting a ``key_facts["status"]`` no record has ever carried went unnoticed. These tests
build the body the production registry registers and await it against a real
:class:`~agentdag.application.kernel.context.Coordinator` over a temporary run directory.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.context import Coordinator
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.application.kernel.ports import ResolvedRow
from agentdag.application.kernel.registry import PlanContext
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.kernel_errors import KernelError
from agentdag.domain.models import Budget, Isolation, Kind, NodeOutcome, NodeSpec, NodeStatus, ResultRecord
from agentdag.domain.plan import Entry
from agentdag.domain.policy import FailureAction

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

REG = build_op_registry()


class OneRowPolicy:
    """A one-row tier policy; no executor is ever needed by the code primitives below."""

    version: str = "sha256:test"
    max_turns: int = 5
    max_attempts: int = 1
    max_continuations: int = 3
    deny_bash: tuple[str, ...] = ()
    on_auth_failure: FailureAction = FailureAction.FAIL_RUN
    on_rate_limit: FailureAction = FailureAction.SUSPEND_RUN
    tokens_per_row: Mapping[str, int] = {}
    deadline_ceiling_s: float = 999_999.0

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        del spec
        return ResolvedRow(alias="sonnet", executor="claude", handover_at_tokens=100_000)


def coordinator(tmp_path: Path) -> Coordinator:
    """Build a coordinator over a fresh run directory, with no executors wired."""
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "r1")
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    return Coordinator(
        run_id="r1",
        workflow="t",
        args={},
        dispatcher=Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock()),
        run_dir=run_dir,
        clock=UtcClock(),
        executors={},
        gate_port=MakeTestGate(command=(sys.executable, "-c", "raise SystemExit(0)")),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=OneRowPolicy(),
        sandbox=NoSandbox(),
        parallel=1,
    )


def code_spec(node_id: str, kind: Kind = Kind.REDUCE) -> NodeSpec:
    """Build a code-node spec these tests dispatch."""
    return NodeSpec(
        node_id=node_id,
        kind=kind,
        executor="code",
        isolation=Isolation.NONE,
        deps=[],
        write_set=[],
        deadline_s=60,
        budget=Budget(),
    )


def op_entry(op: str, node_id: str, args: Mapping[str, object] | None = None) -> Entry:
    """Build the plan entry a registered op's ``build`` is handed."""
    return Entry(
        spec=code_spec(node_id, _KIND_OF.get(op, Kind.REDUCE)),
        op=op,
        args=dict(args or {}),
        brief="do it",
        output_contract=frozenset(),
    )


_KIND_OF: dict[str, Kind] = {"scan": Kind.GATE, "approve": Kind.APPROVE, "reduce:count": Kind.REDUCE}


async def seed(co: Coordinator, node_id: str, status: NodeStatus) -> ResultRecord:
    """Dispatch one trivial code node so the run holds a real record with ``status``."""

    def fold() -> NodeOutcome:
        return NodeOutcome(
            status=status,
            key_facts={"seeded": node_id},
            typed_fields=["seeded"],
            executor_used="code",
            model_used="-",
            effort_used="-",
        )

    return await co.reduce(code_spec(node_id), fold=fold)


async def count_over(co: Coordinator, run_root: Path) -> int:
    """Build ``reduce:count``'s registered body, await it, and return the count it recorded."""
    body = REG.get("reduce:count").build(op_entry("reduce:count", "r_count"), PlanContext(co=co, cwd=run_root))
    record = await body()
    assert isinstance(record, ResultRecord)
    return int(record.key_facts["count"])


def test_reduce_count_counts_the_records_that_actually_passed(tmp_path: Path) -> None:
    """CRITICAL 2: the fold counted ``key_facts["status"] == "passed"``, which nothing writes.

    ``status`` is a TOP-LEVEL record field and its passing member is ``NodeStatus.DONE``;
    ``"passed"`` is not a member of that enum at all, so the old fold returned 0 forever
    and no test noticed because no test ever invoked a built body.
    """
    co = coordinator(tmp_path)

    async def run() -> int:
        await seed(co, "a", NodeStatus.DONE)
        await seed(co, "b", NodeStatus.DONE)
        await seed(co, "c", NodeStatus.FAILED)
        return await count_over(co, co.run_dir.root)

    assert asyncio.run(run()) == 2


def test_reduce_count_is_zero_when_nothing_passed(tmp_path: Path) -> None:
    """The other direction, so the test above cannot pass by counting everything."""
    co = coordinator(tmp_path)

    async def run() -> int:
        await seed(co, "a", NodeStatus.FAILED)
        await seed(co, "b", NodeStatus.CANCELLED)
        return await count_over(co, co.run_dir.root)

    assert asyncio.run(run()) == 0


def test_build_time_arg_errors_are_kernel_errors(tmp_path: Path) -> None:
    """MINOR 10: a caller catching the kernel's own error type must see these.

    ``_build_scan`` and ``_build_approve`` re-validate their args when the body is BUILT
    (they need the parsed values to close over), and a raw pydantic ``ValidationError``
    escaping there is a different family from everything else the kernel raises.
    """
    ctx = PlanContext(co=coordinator(tmp_path), cwd=tmp_path)
    with pytest.raises(KernelError):
        REG.get("scan").build(op_entry("scan", "s0", {"watched": 5}), ctx)
    with pytest.raises(KernelError):
        REG.get("approve").build(op_entry("approve", "a0", {}), ctx)
