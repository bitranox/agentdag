"""Tests for the code primitives: gate, reduce, map, stage/apply, and approve (minimal).

The journal, the run directory, the clock, the gate and git are the REAL adapters, as in
``test_kernel_context.py``; a fake one-row policy is the only fake here, because these
are code primitives - no executor is needed to exercise them.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.context import Coordinator, HasDedupKey
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.application.kernel.ports import ResolvedRow
from agentdag.domain.errors import SpecRejected, Suspended
from agentdag.domain.graph_a import PushIntent
from agentdag.domain.keys import content_hash
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
    ResultRecord,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class OneRowPolicy:
    """A one-row tier policy: every spec resolves to the sonnet row on the claude executor."""

    version: str = "sha256:test"
    max_turns: int = 5
    deny_bash: tuple[str, ...] = ("git push",)
    tokens_per_row: Mapping[str, int] = {"sonnet": 10}

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
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
    if rd is not None:
        run_dir = rd
    else:
        base = tmp_path / "runs"
        base.mkdir(parents=True, exist_ok=True)
        run_dir = FsRunDir.create(base, "r1")
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    co = Coordinator(
        run_id="r1",
        workflow="t",
        args={},
        dispatcher=Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock()),
        run_dir=run_dir,
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
    return co, run_dir


def code(node_id: str, kind: Kind, deps: list[str] | None = None, write_set: list[str] | None = None) -> NodeSpec:
    """Build a code-node spec these tests dispatch."""
    return NodeSpec(
        node_id=node_id,
        kind=kind,
        executor="code",
        isolation=Isolation.NONE,
        deps=deps or [],
        write_set=write_set or [],
        deadline_s=60,
        budget=Budget(),
    )


def _done(**key_facts: object) -> NodeOutcome:
    """A trivial DONE outcome for a cheap declaration dispatch (``reduce``'s ``fold``)."""
    return NodeOutcome(
        status=NodeStatus.DONE,
        key_facts=key_facts,
        typed_fields=list(key_facts),
        executor_used="code",
        model_used="-",
        effort_used="-",
    )


@pytest.mark.os_agnostic
def test_gate_records_the_exit_code_and_the_log(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path, gate_rc=3)

    r = asyncio.run(co.gate(code("g_test@1", Kind.GATE), argv=("make", "test"), cwd=rd.root))

    assert r.status == NodeStatus.FAILED
    assert r.key_facts["rc"] == 3
    assert (rd.root / r.artefact_refs[0]).exists()


@pytest.mark.os_agnostic
def test_gate_records_done_on_exit_code_zero(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path, gate_rc=0)

    r = asyncio.run(co.gate(code("g_ok@1", Kind.GATE), argv=("make", "test"), cwd=rd.root))

    assert r.status == NodeStatus.DONE


@pytest.mark.os_agnostic
def test_map_contains_a_raising_branch_and_still_returns_every_other_record(tmp_path: Path) -> None:
    co, _ = coordinator(tmp_path)

    async def body(i: int, item: str) -> ResultRecord:
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

    assert [r.status for r in records] == [NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.DONE]
    assert records[1].error is not None
    assert records[1].node_id == "m@1"


@pytest.mark.os_agnostic
def test_stage_writes_intents_before_apply_and_apply_is_idempotent(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    intents = [PushIntent(repo=Path("/s/origin/a.git"), head_sha="a" * 40, dedup_key="a.git-" + "a" * 40)]

    s = asyncio.run(co.stage(code("s", Kind.STAGE), intents=intents, kind="push"))

    assert (rd.intents_dir("push") / ("a.git-" + "a" * 40 + ".json")).exists()
    assert s.key_facts["count"] == 1

    performed: list[str] = []

    def push_and_record(intent: HasDedupKey) -> str:
        performed.append(intent.dedup_key)
        return "pushed"

    a1 = asyncio.run(
        co.apply(code("ap", Kind.APPLY, deps=["s"]), intents=intents, kind="push", perform=push_and_record)
    )
    a2 = asyncio.run(
        co.apply(code("ap2", Kind.APPLY, deps=["s"]), intents=intents, kind="push", perform=push_and_record)
    )

    assert performed == ["a.git-" + "a" * 40]
    assert a1.key_facts["outcomes"] == {"a.git-" + "a" * 40: "pushed"}
    assert a2.key_facts["outcomes"] == {"a.git-" + "a" * 40: "already-done"}


@pytest.mark.os_agnostic
def test_apply_with_a_raising_perform_yields_a_failed_record_and_no_marker(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    intents = [PushIntent(repo=Path("/s/origin/b.git"), head_sha="b" * 40, dedup_key="b.git-" + "b" * 40)]
    asyncio.run(co.stage(code("s2", Kind.STAGE), intents=intents, kind="push2"))

    def boom(intent: HasDedupKey) -> str:
        raise RuntimeError("push exploded")

    r = asyncio.run(co.apply(code("ap3", Kind.APPLY, deps=["s2"]), intents=intents, kind="push2", perform=boom))

    assert r.status == NodeStatus.FAILED
    assert not rd.marker("push2", "b.git-" + "b" * 40).exists()


@pytest.mark.os_agnostic
def test_scan_treats_the_watched_nodes_own_bookkeeping_and_a_siblings_declared_write_set_as_allowed(
    tmp_path: Path,
) -> None:
    co, rd = coordinator(tmp_path)

    # A cheap declaration dispatch: reduce runs synchronously, through the same
    # _dispatch path a real work/map branch uses, so declared_write_sets is filled
    # exactly as it would be for a real dispatched node.
    asyncio.run(co.reduce(code("w@1", Kind.REDUCE, write_set=["wt/a/**"]), fold=lambda: _done(ok=True)))
    asyncio.run(co.reduce(code("w@2", Kind.REDUCE, write_set=["wt/b/**"]), fold=lambda: _done(ok=True)))

    (rd.root / "wt/a").mkdir(parents=True)
    (rd.root / "wt/a/existing.py").write_text("x")

    before = co.snapshot()

    (rd.root / "wt/a/f.py").write_text("new")  # (i) declared for w@1: allowed
    (rd.root / "nodes/w@1/abcd1234").mkdir(parents=True)
    (rd.root / "nodes/w@1/abcd1234/record.json").write_text("{}")  # (ii) node bookkeeping: allowed
    (rd.root / "wt/b").mkdir(parents=True)
    (rd.root / "wt/b/g.py").write_text("y")  # w@2's declared write set (a sibling under parallel > 1): allowed
    (rd.root / "wt/other").mkdir(parents=True)
    (rd.root / "wt/other/STRAY").write_text("nope")  # (iii) undeclared: a finding
    (rd.root / "wt/a/existing.py").chmod(0o755)  # (iv) mode only: not a finding

    r = asyncio.run(co.scan(code("g_scan@1", Kind.GATE), watched="w@1", before=before, write_set=["wt/a/**"]))

    assert r.status == NodeStatus.FAILED
    assert r.key_facts["stray"] == ["wt/other/STRAY"]

    before2 = co.snapshot()
    (rd.root / "wt/a/f2.py").write_text("z")

    r2 = asyncio.run(co.scan(code("g_scan@2", Kind.GATE), watched="w@1", before=before2, write_set=["wt/a/**"]))

    assert r2.status == NodeStatus.DONE
    assert r2.key_facts["stray"] == []


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
    assert co2.dispatcher.index.decisions["a"].by == "me"

    co2.fold_decisions()  # a second call, no new decision file: the folded decision must not duplicate

    assert len(co2.dispatcher.index.decisions) == 1


@pytest.mark.os_agnostic
def test_approve_refuses_a_default_with_an_external_effect(tmp_path: Path) -> None:
    co, _ = coordinator(tmp_path)

    with pytest.raises(SpecRejected):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(default="approve")))


@pytest.mark.os_agnostic
def test_approve_accepts_a_decision_whose_payload_hash_matches_the_payload_on_offer(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    with pytest.raises(Suspended):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))
    matching_hash = content_hash(payload().model_dump_json(indent=1))
    rd.write_decision(Decision(node_id="a", decision="approve", by="me", token_id="local", payload_hash=matching_hash))
    co2, _ = coordinator(tmp_path, rd=rd)
    co2.fold_decisions()

    result = asyncio.run(co2.approve(code("a", Kind.APPROVE), payload=payload()))

    assert result.decision == "approve"


@pytest.mark.os_agnostic
def test_approve_refuses_a_decision_made_for_a_different_payload(tmp_path: Path) -> None:
    # M3's retry turning a failed repo into a passed one, or a worktree edited by hand between
    # the suspend and the resume, changes the push list; a stale approval must never carry over.
    co, rd = coordinator(tmp_path)
    with pytest.raises(Suspended):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))
    stale_hash = "sha256:" + "0" * 64
    rd.write_decision(Decision(node_id="a", decision="approve", by="me", token_id="local", payload_hash=stale_hash))
    co2, _ = coordinator(tmp_path, rd=rd)
    co2.fold_decisions()

    with pytest.raises(SpecRejected, match="different payload"):
        asyncio.run(co2.approve(code("a", Kind.APPROVE), payload=payload()))

    assert not list(rd.root.glob("nodes/a/*/record.json"))  # refused before anything was dispatched


@pytest.mark.os_agnostic
def test_fold_decisions_ignores_a_reserved_cancel_file(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    rd.decisions_dir.mkdir(parents=True, exist_ok=True)
    (rd.decisions_dir / "a.cancel.json").write_text('{"node_id": "a", "reason": "stop"}')
    (rd.decisions_dir / "_run.cancel.json").write_text('{"reason": "stop everything"}')

    lines_before = len(co.dispatcher.journal.lines())

    co.fold_decisions()  # must not raise, and must not journal or count either file

    assert len(co.dispatcher.journal.lines()) == lines_before
    assert "a" not in co.dispatcher.index.decisions
