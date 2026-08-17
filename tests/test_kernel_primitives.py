"""Tests for the code primitives: gate, reduce, map, stage/apply, and approve (minimal).

The journal, the run directory, the clock, the gate and git are the REAL adapters, as in
``test_kernel_context.py``; a fake one-row policy is the only fake here, because these
are code primitives - no executor is needed to exercise them.
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

if TYPE_CHECKING:
    from pathlib import Path

    from agentdag.application.kernel.ports import ResolvedRow


class OneRowPolicy:
    """A one-row tier policy: every spec resolves to the sonnet row on the claude executor."""

    version = "sha256:test"
    max_turns = 5
    deny_bash = ("git push",)
    tokens_per_row = {"sonnet": 10}

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        from agentdag.application.kernel.ports import ResolvedRow

        return ResolvedRow(alias="sonnet", executor="claude")


def coordinator(tmp_path: Path, *, gate_rc: int = 0, rd: FsRunDir | None = None) -> tuple[Coordinator, FsRunDir]:
    """Build a coordinator with no executors wired - these primitives never need one.

    Args:
        tmp_path: Where a fresh run directory is created, when ``rd`` is not given.
        gate_rc: The exit code the fake gate command raises with.
        rd: An existing run directory to build over, instead of a fresh one - what a
            relaunch does, so a second coordinator over the SAME run sees the same
            journal, decisions and node directories as the first.

    Returns:
        The coordinator, and the run directory it was built over.
    """
    run_dir = rd if rd is not None else FsRunDir.create(tmp_path / "runs", "r1")
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    co = Coordinator(
        run_id="r1",
        workflow="t",
        args={},
        dispatcher=Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock()),
        run_dir=run_dir,
        clock=UtcClock(),
        executors={},
        gate_port=MakeTestGate(lock=tmp_path / "gate.lock", command=(sys.executable, "-c", f"raise SystemExit({gate_rc})")),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=OneRowPolicy(),
        parallel=2,
    )
    return co, run_dir


def code(node_id: str, kind: Kind, deps: list[str] | None = None) -> NodeSpec:
    """Build a code-node spec these tests dispatch."""
    return NodeSpec(node_id=node_id, kind=kind, executor="code", isolation=Isolation.NONE, deps=deps or [], deadline_s=60, budget=Budget())


@pytest.mark.os_agnostic
def test_gate_records_the_exit_code_and_the_log(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path, gate_rc=3)

    r = asyncio.run(co.gate(code("g_test@1", Kind.GATE), argv=("make", "test"), cwd=rd.root))

    assert r.status == NodeStatus.FAILED
    assert r.key_facts["rc"] == 3
    assert (rd.root / r.artefact_refs[0]).exists()


@pytest.mark.os_agnostic
def test_map_contains_a_raising_branch_and_still_returns_every_other_record(tmp_path: Path) -> None:
    co, _ = coordinator(tmp_path)

    async def body(i: int, item: str) -> object:
        if item == "bad":
            raise RuntimeError("clone exploded")
        return await co.reduce(
            code(f"m@{i}", Kind.REDUCE),
            fold=lambda i=i: NodeOutcome(
                status=NodeStatus.DONE,
                key_facts={"i": i},
                typed_fields=["i"],
                artefact_refs=["x"],
                executor_used="code",
                model_used="-",
                effort_used="-",
            ),
        )

    records = asyncio.run(co.map("m", ["a", "bad", "c"], body))  # type: ignore[arg-type]

    assert [r.status for r in records] == [NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.DONE]
    assert records[1].error is not None


@pytest.mark.os_agnostic
def test_stage_writes_intents_before_apply_and_apply_is_idempotent(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    intents = [PushIntent(repo=rd.root / "s/origin/a.git", head_sha="a" * 40, dedup_key="a.git-" + "a" * 40)]

    s = asyncio.run(co.stage(code("s", Kind.STAGE), intents=intents, kind="push"))

    assert (rd.intents_dir("push") / ("a.git-" + "a" * 40 + ".json")).exists()
    assert s.key_facts["count"] == 1

    performed: list[str] = []

    def do_push(it: PushIntent) -> str:
        performed.append(it.dedup_key)
        return "pushed"

    a1 = asyncio.run(co.apply(code("ap", Kind.APPLY, deps=["s"]), intents=intents, kind="push", perform=do_push))  # type: ignore[arg-type]
    a2 = asyncio.run(co.apply(code("ap2", Kind.APPLY, deps=["s"]), intents=intents, kind="push", perform=do_push))  # type: ignore[arg-type]

    assert performed == ["a.git-" + "a" * 40]
    assert a1.key_facts["outcomes"] == {"a.git-" + "a" * 40: "pushed"}
    assert a2.key_facts["outcomes"] == {"a.git-" + "a" * 40: "already-done"}


def payload(default: str = "hold") -> ApprovePayload:
    """Build the approve payload these tests suspend and resolve."""
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


@pytest.mark.os_agnostic
def test_approve_suspends_without_a_decision_and_returns_it_when_one_is_journaled(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)

    with pytest.raises(Suspended) as info:
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))

    assert info.value.node_id == "a"
    assert list(rd.root.glob("nodes/a/*/payload.json"))

    rd.write_decision(Decision(node_id="a", decision="approve", by="me", token_id="local"))
    co2, _ = coordinator(tmp_path, rd=rd)
    co2.fold_decisions()  # what run.py does on relaunch

    result = asyncio.run(co2.approve(code("a", Kind.APPROVE), payload=payload()))

    assert result.decision == "approve"
    assert co2.interactions == 1


@pytest.mark.os_agnostic
def test_approve_refuses_a_default_with_an_external_effect(tmp_path: Path) -> None:
    co, _ = coordinator(tmp_path)

    with pytest.raises(SpecRejected):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(default="approve")))
