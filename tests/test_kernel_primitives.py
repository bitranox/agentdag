"""Tests for the code primitives: gate, reduce, map, stage/apply, and approve (minimal).

The journal, the run directory, the clock, the gate and git are the REAL adapters, as in
``test_kernel_context.py``; a fake one-row policy is the only fake here, because these
are code primitives - no executor is needed to exercise them.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.approve import (
    DEADLINE_REASON,
    MAX_BODY_LINE_OCTETS,
    MAX_OPERATOR_TEXT_CHARS,
    SYSTEM_IDENTITY,
    TIMER_TOKEN_ID,
    DeadlineOutcome,
    apply_due_default,
    render_for_operator,
    validate_approve_payload,
)
from agentdag.application.kernel.context import Coordinator, HasDedupKey
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.application.kernel.ports import ResolvedRow
from agentdag.application.kernel.summary import append_run_summary
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.graph_a import PushIntent
from agentdag.domain.journal import ApproveDecisionLine, RunSummaryLine
from agentdag.domain.kernel_errors import LockHeld, RunRefused, SpecRejected, Suspended
from agentdag.domain.keys import content_hash, hash8
from agentdag.domain.models import (
    ApproveOption,
    ApprovePayload,
    Budget,
    Decision,
    Isolation,
    Kind,
    MarkerPhase,
    NodeOutcome,
    NodeSpec,
    NodeStatus,
    ResultRecord,
    RetryGrant,
    RunState,
    RunStatus,
)
from agentdag.domain.policy import FailureAction, RunLimits


class OneRowPolicy:
    """A one-row tier policy: every spec resolves to the sonnet row on the claude executor."""

    version: str = "sha256:test"
    max_turns: int = 5
    default_node_tokens: int | None = None
    """No default cap: these doubles pin what a node's OWN declared budget does,
    and a default would silently cap every spec that declares none."""
    max_attempts: int = 1
    max_continuations: int = 3  # these tests assert one dispatch per node
    deny_bash: tuple[str, ...] = ("git push",)
    on_auth_failure: FailureAction = FailureAction.FAIL_RUN
    on_rate_limit: FailureAction = FailureAction.SUSPEND_RUN
    run_limits: RunLimits = RunLimits(
        tokens_per_row={"sonnet": 10},
        deadline_ceiling_s=999_999.0,
        per_kind_ceiling={},
        planner_kinds=[],
        top_role_budget_floor=0.0,
        max_replans=3,
        max_nodes_per_run=1000,
        max_nodes_per_plan=1000,
        max_plan_depth=5,
    )

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        return ResolvedRow(alias="sonnet", executor="claude", handover_at_tokens=100_000)


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
        gate_port=MakeTestGate(command=(sys.executable, "-c", f"raise SystemExit({gate_rc})")),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=OneRowPolicy(),
        registry=build_op_registry(),
        sandbox=NoSandbox(),
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
def test_two_concurrent_maps_never_exceed_parallel_dispatches_between_them(tmp_path: Path) -> None:
    # `parallel` is what the HOST may run at once (worktrees, the shared bmk tool env, the
    # executor's own concurrency), so it has to hold across the whole run: a per-map
    # semaphore lets a workflow that fans out twice at the same time run 2 x parallel
    # branches. The counter is raised on entry and lowered on exit inside the branch body,
    # so its high-water mark IS the real concurrency, not an inference from timings.
    co, _ = coordinator(tmp_path)  # parallel=2
    in_flight = 0
    peak = 0

    async def body(_: int, __: str) -> ResultRecord:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)  # long enough that every admitted branch overlaps the others
        in_flight -= 1
        return _record()

    async def both_maps() -> None:
        await asyncio.gather(
            co.map("m1", ["a", "b", "c"], body),
            co.map("m2", ["d", "e", "f"], body),
        )

    asyncio.run(both_maps())

    assert peak <= co.parallel


def _record() -> ResultRecord:
    """A minimal DONE record a map branch can return without dispatching anything."""
    return ResultRecord(
        node_id="b",
        attempt=0,
        status=NodeStatus.DONE,
        artefact_refs=["x"],
        key_facts={},
        typed_fields=[],
        tokens=None,
        charged_tokens={},
        cost_usd=None,
        duration_s=0.0,
        executor_used="code",
        model_used="-",
        effort_used="-",
        knowledge_used=[],
        input_hash="sha256:0",
    )


@pytest.mark.os_agnostic
def test_stage_writes_intents_before_apply_and_apply_is_idempotent(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    intents = [PushIntent(repo=Path("/s/origin/a.git"), head_sha="a" * 40, dedup_key="a.git-" + "a" * 40)]

    s = asyncio.run(co.stage(code("s", Kind.STAGE), intents=intents, kind="push"))

    assert (rd.intents_dir("push") / ("a.git-" + "a" * 40 + ".json")).exists()
    assert s.key_facts["count"] == 1

    performed: list[tuple[str, bool]] = []

    def push_and_record(intent: HasDedupKey, *, may_have_landed: bool) -> str:
        performed.append((intent.dedup_key, may_have_landed))
        return "pushed"

    a1 = asyncio.run(
        co.apply(code("ap", Kind.APPLY, deps=["s"]), intents=intents, kind="push", perform=push_and_record)
    )
    a2 = asyncio.run(
        co.apply(code("ap2", Kind.APPLY, deps=["s"]), intents=intents, kind="push", perform=push_and_record)
    )

    # Performed once, and told it was a FIRST attempt: the done marker short-circuits the
    # second call, so nothing ever sees may_have_landed on a completed effect.
    assert performed == [("a.git-" + "a" * 40, False)]
    assert a1.key_facts["outcomes"] == {"a.git-" + "a" * 40: "pushed"}
    assert a1.key_facts["resumed"] == []
    assert a2.key_facts["outcomes"] == {"a.git-" + "a" * 40: "already-done"}


@pytest.mark.os_agnostic
def test_an_effect_that_cannot_be_read_back_refuses_the_repeat_the_next_launch_would_make(
    tmp_path: Path,
) -> None:
    """The reason the attempted marker exists: a perform with no external state to consult.

    Sending a mail or calling a non-idempotent API cannot ask the target whether it already
    happened, so nothing the kernel does with markers alone makes a repeat safe. What the
    kernel CAN do is tell it, and this pins that the fact arrives - a run that crashed inside
    the effect refuses on the next launch instead of doing it twice.

    The control is the first launch in the same test: told may_have_landed is false, the same
    perform DOES the effect. Without it a perform that always refused would pass.
    """
    co, rd = coordinator(tmp_path)
    key = "mail-" + "c" * 8
    intents = [PushIntent(repo=Path("/s/origin/c.git"), head_sha="c" * 40, dedup_key=key)]
    asyncio.run(co.stage(code("s3", Kind.STAGE), intents=intents, kind="mail"))
    sent: list[str] = []

    def send_or_refuse(intent: HasDedupKey, *, may_have_landed: bool) -> str:
        if may_have_landed:
            raise RuntimeError(f"{intent.dedup_key} may already have been sent; refusing to send it twice")
        sent.append(intent.dedup_key)
        raise SystemExit(9)  # the crash, after the effect and before the done marker

    with pytest.raises(SystemExit):
        asyncio.run(
            co.apply(code("ap4", Kind.APPLY, deps=["s3"]), intents=intents, kind="mail", perform=send_or_refuse)
        )

    assert sent == [key]  # control: a first attempt is told false, and performs
    assert rd.marker("mail", key, phase=MarkerPhase.ATTEMPTED).exists()
    assert not rd.marker("mail", key).exists()

    r = asyncio.run(
        co.apply(code("ap5", Kind.APPLY, deps=["s3"]), intents=intents, kind="mail", perform=send_or_refuse)
    )

    assert sent == [key]  # not sent a second time
    assert r.status == NodeStatus.FAILED
    # Failed for the REFUSAL, not for some other error on the same path - and the record
    # carries no key_facts at all when the body raised, which is why the reason is read here.
    assert r.error is not None
    assert "may already have been sent" in r.error.message
    assert rd.marker("mail", key, phase=MarkerPhase.ATTEMPTED).exists()
    assert not rd.marker("mail", key).exists()


@pytest.mark.os_agnostic
def test_apply_with_a_raising_perform_yields_a_failed_record_and_no_marker(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    intents = [PushIntent(repo=Path("/s/origin/b.git"), head_sha="b" * 40, dedup_key="b.git-" + "b" * 40)]
    asyncio.run(co.stage(code("s2", Kind.STAGE), intents=intents, kind="push2"))

    def boom(intent: HasDedupKey, *, may_have_landed: bool) -> str:
        raise RuntimeError("push exploded")

    r = asyncio.run(co.apply(code("ap3", Kind.APPLY, deps=["s2"]), intents=intents, kind="push2", perform=boom))

    assert r.status == NodeStatus.FAILED
    assert not rd.marker("push2", "b.git-" + "b" * 40).exists()
    # The attempted marker DOES stay: perform was entered, so whether the effect landed is
    # exactly what nobody can know - which is the state the next launch has to be told about.
    assert rd.marker("push2", "b.git-" + "b" * 40, phase=MarkerPhase.ATTEMPTED).exists()


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


def payload(default: str = "hold", *, text: str = "push?") -> ApprovePayload:
    """Build the approve payload these tests suspend and resolve.

    ``text`` is what a test varies to make a DIFFERENT payload for the same node - the
    changed push list design 3.4's binding exists for.
    """
    return ApprovePayload(
        text=text,
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


def payload_hash_of(p: ApprovePayload) -> str:
    """Hash a payload exactly as ``Coordinator.approve`` does, so a test can bind a decision to it."""
    return content_hash(p.model_dump_json(indent=1))


def approved(
    node_id: str,
    p: ApprovePayload,
    *,
    verdict: str = "approve",
    by: str = "me",
    token_id: str = "local",  # noqa: S107 - a token IDENTITY, not a secret
) -> Decision:
    """Build a decision bound to ``p``: the pair (node id, payload hash) IS its identity.

    ``by`` is the decider's IDENTITY and ``token_id`` the agent that applied it; a
    decision the SYSTEM applied carries the reserved identity, which is what the run
    summary reads to tell an unattended default from a human answer.
    """
    return Decision(node_id=node_id, decision=verdict, by=by, token_id=token_id, payload_hash=payload_hash_of(p))


@pytest.mark.os_agnostic
def test_approve_suspends_without_a_decision_and_returns_it_when_one_is_journaled(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)

    with pytest.raises(Suspended) as info:
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))

    assert info.value.node_id == "a"
    assert info.value.payload_hash == payload_hash_of(payload())  # WHICH payload to answer
    assert list(rd.root.glob(f"nodes/a/{hash8(payload_hash_of(payload()))}/payload.json"))

    rd.write_decision(approved("a", payload()))
    co2, _ = coordinator(tmp_path, rd=rd)
    co2.fold_decisions()  # what run.py does on relaunch

    result = asyncio.run(co2.approve(code("a", Kind.APPROVE), payload=payload()))

    assert result.decision == "approve"
    assert result.payload_hash == payload_hash_of(payload())  # the decision names what it was applied to
    assert co2.dispatcher.index.decisions["a", payload_hash_of(payload())].by == "me"

    co2.fold_decisions()  # a second call, no new decision file: the folded decision must not duplicate

    assert len(co2.dispatcher.index.decisions) == 1


@pytest.mark.os_agnostic
def test_approve_refuses_a_default_with_an_external_effect(tmp_path: Path) -> None:
    co, _ = coordinator(tmp_path)

    with pytest.raises(SpecRejected):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(default="approve")))


@pytest.mark.os_agnostic
def test_a_decision_for_an_old_payload_suspends_again_on_the_new_one_and_dispatches_nothing(
    tmp_path: Path,
) -> None:
    # M3's retry turning a failed repo into a passed one, or a worktree edited by hand between
    # the suspend and the resume, changes the push list. The old approval is not an approval of
    # the new list, so the run must ASK again - and it must leave the new payload on disk to ask
    # about, which the earlier refuse-and-tell-them-to-approve-again shape never did.
    co, rd = coordinator(tmp_path)
    old, new = payload(), payload(text="push? (one repo more)")
    with pytest.raises(Suspended):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=old))
    rd.write_decision(approved("a", old))
    co2, _ = coordinator(tmp_path, rd=rd)
    co2.fold_decisions()

    with pytest.raises(Suspended) as info:
        asyncio.run(co2.approve(code("a", Kind.APPROVE), payload=new))

    assert info.value.payload_hash == payload_hash_of(new)
    new_payload_file = rd.root / "nodes" / "a" / hash8(payload_hash_of(new)) / "payload.json"
    assert json.loads(new_payload_file.read_text(encoding="utf-8"))["text"] == new.text
    assert not list(rd.root.glob("nodes/a/*/record.json"))  # suspended before anything was dispatched

    # ... and answering the NEW payload resumes it, with no file deleted and no hand editing.
    rd.write_decision(approved("a", new))
    co3, _ = coordinator(tmp_path, rd=rd)
    co3.fold_decisions()

    result = asyncio.run(co3.approve(code("a", Kind.APPROVE), payload=new))

    assert result.decision == "approve"
    assert result.payload_hash == payload_hash_of(new)
    assert len(co3.dispatcher.index.decisions) == 2  # both answers, under their own payload hashes


@pytest.mark.os_agnostic
def test_a_second_decision_on_the_same_payload_is_refused_write_once(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    with pytest.raises(Suspended):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))
    rd.write_decision(approved("a", payload(), verdict="hold"))

    with pytest.raises(FileExistsError):
        rd.write_decision(approved("a", payload(), verdict="approve"))

    co2, _ = coordinator(tmp_path, rd=rd)
    co2.fold_decisions()
    result = asyncio.run(co2.approve(code("a", Kind.APPROVE), payload=payload()))
    assert result.decision == "hold"  # the first answer stands


@pytest.mark.os_agnostic
def test_fold_decisions_of_a_file_lacking_payload_hash_raises_run_refused_naming_the_path(tmp_path: Path) -> None:
    # payload_hash is required now - a decision without one has half an identity and cannot
    # even be built (Decision(...) would refuse it). A hand-placed file that lacks it must
    # refuse LOUDLY, never be silently skipped or answered as if bound to whatever payload
    # happens to be on offer - the pre-binding fallback this design used to have is gone.
    co, rd = coordinator(tmp_path)
    with pytest.raises(Suspended):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))
    bad_path = rd.decisions_dir / "a.11111111.json"
    bad_path.write_text(
        json.dumps({"node_id": "a", "decision": "approve", "reason": "", "by": "me", "token_id": "local"}),
        encoding="utf-8",
    )

    co2, _ = coordinator(tmp_path, rd=rd)
    with pytest.raises(RunRefused, match=re.escape(str(bad_path))):
        co2.fold_decisions()
    with pytest.raises(RunRefused, match=re.escape(str(bad_path))):
        rd.list_decisions()


@pytest.mark.os_agnostic
def test_human_interactions_counts_decisions_not_nodes(tmp_path: Path) -> None:
    # One approve node, two payloads, two answers: the human was asked twice, so the run summary
    # must say 2. Keyed by node id alone this read 1.
    co, rd = coordinator(tmp_path)
    first, second = payload(), payload(text="push? (one repo more)")
    rd.write_decision(approved("a", first))
    rd.write_decision(approved("a", second, verdict="hold"))
    co.fold_decisions()

    append_run_summary(co, replay_seconds=None)

    summary = co.dispatcher.journal.lines()[-1]
    assert isinstance(summary, RunSummaryLine)
    assert summary.human_interactions == 2


@pytest.mark.os_agnostic
def test_a_system_decision_is_not_a_human_interaction(tmp_path: Path) -> None:
    # The control for the test above: it must be able to report a number OTHER than the
    # decision count, or "counts decisions" is not what it proves. Built the way the
    # deadline owner actually builds one (Task 22) - the reserved `by` identity, and a
    # `token_id` naming the agent that applied it - rather than a hand-written sentinel.
    co, rd = coordinator(tmp_path)
    rd.write_decision(approved("a", payload(), by=SYSTEM_IDENTITY, token_id=TIMER_TOKEN_ID))
    co.fold_decisions()

    append_run_summary(co, replay_seconds=None)

    summary = co.dispatcher.journal.lines()[-1]
    assert isinstance(summary, RunSummaryLine)
    assert summary.human_interactions == 0


@pytest.mark.os_agnostic
def test_fold_decisions_ignores_a_reserved_cancel_file(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    rd.decisions_dir.mkdir(parents=True, exist_ok=True)
    (rd.decisions_dir / "a.cancel.json").write_text('{"node_id": "a", "reason": "stop"}')
    (rd.decisions_dir / "_run.cancel.json").write_text('{"reason": "stop everything"}')

    lines_before = len(co.dispatcher.journal.lines())

    co.fold_decisions()  # must not raise, and must not journal or count either file

    assert len(co.dispatcher.journal.lines()) == lines_before
    assert co.dispatcher.index.decisions == {}


@pytest.mark.os_agnostic
def test_fold_decisions_skips_an_already_folded_pair_by_filename_before_parsing_it(tmp_path: Path) -> None:
    # A file corrupted AFTER it was folded must never block a later launch: fold_decisions
    # decides "already folded" from the FILENAME (node id, short hash) alone, matched against
    # the journal's own folded lines - it must never need to open the file to make that call.
    co, rd = coordinator(tmp_path)
    with pytest.raises(Suspended):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))
    rd.write_decision(approved("a", payload()))
    co2, _ = coordinator(tmp_path, rd=rd)
    co2.fold_decisions()  # folds the real file once

    stub = rd.decisions_dir / f"a.{hash8(payload_hash_of(payload()))}.json"
    stub.write_text("not json at all", encoding="utf-8")  # corrupted AFTER folding

    co3, _ = coordinator(tmp_path, rd=rd)  # its journal already carries the folded line
    co3.fold_decisions()  # must NOT raise: the pair is already folded, so the corrupt file
    # is never opened.

    result = asyncio.run(co3.approve(code("a", Kind.APPROVE), payload=payload()))
    assert result.decision == "approve"


# ---------------------------------------------------------------------------------
# Task 22: the approve deadline's owner. A suspended run's coordinator has EXITED, so
# the default at decide_by is applied by a later, separate pass (agentdag run
# apply-deadlines, driven by the user timer under deploy/). The property that must not
# be got wrong is the RACE: a human decision and this pass arriving for the SAME (node,
# payload hash) - exactly one wins, the loser is REFUSED, and the journal says which.
# ---------------------------------------------------------------------------------


class FixedClock:
    """A :class:`~agentdag.application.kernel.ports.Clock` pinned to one instant.

    The deadline pass reads a clock for exactly one question - has the payload's own
    ``decide_by`` passed - so pinning it is how a test asks that question both ways
    without waiting a day for the shipped 24-hour default.
    """

    def __init__(self, now: datetime) -> None:
        """Bind the instant every :meth:`now` call returns."""
        self._now = now

    def now(self) -> datetime:
        """Return the pinned instant."""
        return self._now


_DECIDE_BY = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)
"""The instant ``payload()`` above puts in its ``decide_by`` field, as a datetime."""

_BEFORE = _DECIDE_BY - timedelta(seconds=1)
_AFTER = _DECIDE_BY + timedelta(seconds=1)


def publish_payload(rd: FsRunDir, p: ApprovePayload) -> str:
    """Write ``p`` where a suspended run's coordinator would have, and return its content hash.

    Byte-identical to what ``Coordinator.approve``'s suspend path writes (the same
    ``model_dump_json(indent=1)`` at the same content-addressed path), so a test can set
    up a payload the coordinator itself would REFUSE to offer - an external-effect
    default - and still have the deadline pass find exactly the shape it reads on disk.
    """
    text = p.model_dump_json(indent=1)
    digest = content_hash(text)
    rd.write_atomic(f"nodes/{p.node_id}/{hash8(digest)}/payload.json", text)
    return digest


def suspended_on(
    rd: FsRunDir, payload_hash: str, *, node_id: str = "a", status: RunStatus = RunStatus.SUSPENDED
) -> None:
    """Write the ``state.json`` a run suspended on ``(node_id, payload_hash)`` carries.

    ``status`` is a parameter because one negative test needs a state file that is NOT
    suspended while still naming a cursor and a payload hash - the only shape in which
    the status check is the single thing standing between the pass and applying a
    default, so a single mutation of that check can actually be seen.
    """
    rd.write_state(
        RunState(
            run_id="r1",
            workflow="t",
            args={},
            owner="tester",
            status=status,
            cursor=node_id,
            cursor_payload_hash=payload_hash,
            policy_version="sha256:test",
        )
    )


def apply_default(rd: FsRunDir, *, now: datetime) -> DeadlineOutcome:
    """Run one deadline pass over ``rd`` with the REAL lock adapter and a pinned clock."""
    return apply_due_default(rd, lock=FileRunLock(), clock=FixedClock(now), holder=current_holder())


@pytest.mark.os_agnostic
def test_the_default_is_applied_once_decide_by_has_passed_and_folds_as_a_system_decision(tmp_path: Path) -> None:
    """The positive control for every refusal below, driven through the real suspend path."""
    co, rd = coordinator(tmp_path)
    with pytest.raises(Suspended):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload()))
    suspended_on(rd, payload_hash_of(payload()))

    outcome = apply_default(rd, now=_AFTER)

    assert outcome.applied is True
    assert outcome.decision == "hold"  # the payload's own default, never a second guess
    assert outcome.node_id == "a"
    assert outcome.reason == ""
    written = rd.read_decision("a", payload_hash_of(payload()))
    assert written is not None
    assert (written.decision, written.by, written.token_id, written.reason) == (
        "hold",
        SYSTEM_IDENTITY,
        TIMER_TOKEN_ID,
        DEADLINE_REASON,
    )

    co2, _ = coordinator(tmp_path, rd=rd)
    co2.fold_decisions()
    folded = [line for line in co2.dispatcher.journal.lines() if isinstance(line, ApproveDecisionLine)]

    assert len(folded) == 1
    assert (folded[0].by, folded[0].reason, folded[0].decision) == (SYSTEM_IDENTITY, DEADLINE_REASON, "hold")
    assert asyncio.run(co2.approve(code("a", Kind.APPROVE), payload=payload())).decision == "hold"


@pytest.mark.os_agnostic
def test_a_default_applied_by_the_deadline_owner_is_not_a_human_interaction(tmp_path: Path) -> None:
    """The run summary counts people, so it must read the decision's IDENTITY, not its token id.

    The deadline owner's ``token_id`` names the TIMER (so a journal reader can tell which
    system applied a default), which is exactly why the count cannot be keyed on that
    field - this is the test that binds the two modules' choice of sentinel together.
    """
    co, rd = coordinator(tmp_path)
    digest = publish_payload(rd, payload())
    suspended_on(rd, digest)
    assert apply_default(rd, now=_AFTER).applied is True
    co.fold_decisions()

    append_run_summary(co, replay_seconds=None)

    summary = co.dispatcher.journal.lines()[-1]
    assert isinstance(summary, RunSummaryLine)
    assert summary.human_interactions == 0


@pytest.mark.os_agnostic
def test_a_human_decision_already_recorded_refuses_the_deadline_default(tmp_path: Path) -> None:
    """The race, human first: the pass loses on the decision file's atomic link, and says so."""
    co, rd = coordinator(tmp_path)
    digest = publish_payload(rd, payload())
    suspended_on(rd, digest)
    rd.write_decision(approved("a", payload(), verdict="approve"))

    outcome = apply_default(rd, now=_AFTER)

    assert outcome.applied is False
    assert "already has a decision" in outcome.reason
    assert outcome.awaiting_decision is True
    standing = rd.read_decision("a", digest)
    assert standing is not None
    assert (standing.decision, standing.by) == ("approve", "me")  # the human's answer stands untouched
    co.fold_decisions()
    folded = [line for line in co.dispatcher.journal.lines() if isinstance(line, ApproveDecisionLine)]
    assert len(folded) == 1  # exactly one decision was recorded for this payload, not two


@pytest.mark.os_agnostic
def test_a_human_answering_after_the_deadline_default_is_refused_write_once(tmp_path: Path) -> None:
    """The race the other way round: the same one-winner rule, whoever gets there first."""
    co, rd = coordinator(tmp_path)
    digest = publish_payload(rd, payload())
    suspended_on(rd, digest)
    assert apply_default(rd, now=_AFTER).applied is True

    with pytest.raises(FileExistsError):
        rd.write_decision(approved("a", payload(), verdict="approve"))

    standing = rd.read_decision("a", digest)
    assert standing is not None
    assert (standing.decision, standing.by) == ("hold", SYSTEM_IDENTITY)
    co.fold_decisions()
    assert len([line for line in co.dispatcher.journal.lines() if isinstance(line, ApproveDecisionLine)]) == 1


@pytest.mark.os_agnostic
def test_no_default_is_applied_before_the_payloads_own_decide_by(tmp_path: Path) -> None:
    """One second short of the deadline is not the deadline - and the reason names when it is."""
    _co, rd = coordinator(tmp_path)
    digest = publish_payload(rd, payload())
    suspended_on(rd, digest)

    outcome = apply_default(rd, now=_BEFORE)

    assert outcome.applied is False
    assert "not due until" in outcome.reason
    assert payload().decide_by in outcome.reason
    assert rd.read_decision("a", digest) is None
    assert rd.decision_files() == []


@pytest.mark.os_agnostic
def test_no_default_is_applied_while_another_process_holds_the_run_lock(tmp_path: Path) -> None:
    """A held lock is evidence a coordinator is working this run, which is not 'unattended'."""
    _co, rd = coordinator(tmp_path)
    digest = publish_payload(rd, payload())
    suspended_on(rd, digest)
    lock = FileRunLock()
    token = lock.acquire(rd.root, current_holder())  # this very process: genuinely alive

    try:
        with pytest.raises(LockHeld):
            lock.acquire(rd.root, current_holder())  # control: the lock really is held
        outcome = apply_due_default(rd, lock=lock, clock=FixedClock(_AFTER), holder=current_holder())
    finally:
        lock.release(token)

    assert outcome.applied is False
    assert "lock" in outcome.reason
    assert rd.read_decision("a", digest) is None


@pytest.mark.os_agnostic
def test_no_default_is_applied_to_a_run_that_is_not_suspended(tmp_path: Path) -> None:
    """A cursor without a suspend is not a question: only a SUSPENDED run is waiting on one.

    The state file here names a cursor and a payload hash while reading ``running``, which
    is a shape the kernel never writes - deliberately, so the status check is the only
    thing between this run and an applied default rather than one of two guards that
    would cover for each other.
    """
    _co, rd = coordinator(tmp_path)
    digest = publish_payload(rd, payload())
    suspended_on(rd, digest, status=RunStatus.RUNNING)

    outcome = apply_default(rd, now=_AFTER)

    assert outcome.applied is False
    assert "not suspended" in outcome.reason
    assert outcome.awaiting_decision is False  # a periodic sweep stays quiet about these
    assert rd.read_decision("a", digest) is None


@pytest.mark.os_agnostic
def test_no_default_is_applied_when_the_payload_on_disk_is_not_the_one_state_json_names(tmp_path: Path) -> None:
    """A hand-edited payload must not talk the pass into applying a default nobody was offered."""
    _co, rd = coordinator(tmp_path)
    digest = publish_payload(rd, payload())
    suspended_on(rd, digest)
    tampered = payload(text="push? (edited by hand after the suspend)")
    rd.write_atomic(f"nodes/a/{hash8(digest)}/payload.json", tampered.model_dump_json(indent=1))

    outcome = apply_default(rd, now=_AFTER)

    assert outcome.applied is False
    assert "does not match state.json" in outcome.reason
    assert rd.read_decision("a", digest) is None


@pytest.mark.os_agnostic
def test_no_default_is_applied_when_the_default_carries_an_external_effect(tmp_path: Path) -> None:
    """Design 2.4's rule, checked where it cashes out: at the moment of unattended application.

    ``Coordinator.approve`` refuses such a payload before it is ever written, so this
    writes one straight to disk - the state a hand-placed file, or a rule that changed
    after the suspend, would leave behind.
    """
    _co, rd = coordinator(tmp_path)
    digest = publish_payload(rd, payload(default="approve"))
    suspended_on(rd, digest)

    outcome = apply_default(rd, now=_AFTER)

    assert outcome.applied is False
    assert "does not name a no-effect option" in outcome.reason
    assert rd.read_decision("a", digest) is None


@pytest.mark.os_agnostic
def test_approve_refuses_a_default_naming_no_option_at_all(tmp_path: Path) -> None:
    """The other half of design 2.4's rule: a default that names nothing is not a default."""
    co, _ = coordinator(tmp_path)

    with pytest.raises(SpecRejected, match="no-effect option"):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(default="not-an-option")))


@pytest.mark.os_agnostic
def test_approve_refuses_operator_text_that_says_nothing(tmp_path: Path) -> None:
    """A payload that asks a human to approve blank space is not a question (decision 8's obligation)."""
    co, _ = coordinator(tmp_path)

    with pytest.raises(SpecRejected, match="empty"):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(text="   \n  ")))


@pytest.mark.os_agnostic
def test_approve_refuses_operator_text_carrying_a_terminal_escape(tmp_path: Path) -> None:
    """An escape sequence rewrites what the terminal shows, so the reader approves something else."""
    co, _ = coordinator(tmp_path)

    with pytest.raises(SpecRejected, match="control character"):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(text="push?\x1b[2K hold")))


@pytest.mark.os_agnostic
def test_approve_refuses_operator_text_carrying_a_bidi_override(tmp_path: Path) -> None:
    """What renders is not what is hashed: the approval binds to bytes the reader never saw."""
    co, _ = coordinator(tmp_path)

    with pytest.raises(SpecRejected, match="invisible or direction-changing"):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(text="push repo-a\u202e repo-b")))


@pytest.mark.os_agnostic
def test_approve_refuses_operator_text_longer_than_a_person_will_read(tmp_path: Path) -> None:
    """A model-authored wall of text is approved unread, which is not an approval."""
    co, _ = coordinator(tmp_path)
    too_long = "\n".join(["  repo-a  0123456789ab"] * 300)

    with pytest.raises(SpecRejected, match="characters"):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(text=too_long)))


@pytest.mark.os_agnostic
def test_approve_refuses_a_line_no_mail_body_may_carry(tmp_path: Path) -> None:
    """RFC 5322 bounds a body line at 998 octets, and this text is a mail body verbatim."""
    co, _ = coordinator(tmp_path)

    with pytest.raises(SpecRejected, match="998"):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(text="r" * 999)))


@pytest.mark.os_agnostic
def test_approve_accepts_a_push_list_at_the_bounds(tmp_path: Path) -> None:
    """The control: a long, legal, multi-line list still reaches the human it is meant for."""
    co, _ = coordinator(tmp_path)
    at_the_line_bound = "r" * 998

    with pytest.raises(Suspended):
        asyncio.run(co.approve(code("a", Kind.APPROVE), payload=payload(text=at_the_line_bound)))


def grant(node_id: str, key: str, *, reason: str = "fixed by hand") -> RetryGrant:
    """Build the grant ``run retry`` records for a failed attempt."""
    return RetryGrant(node_id=node_id, key=key, reason=reason, by="me", token_id="local")


@pytest.mark.os_agnostic
def test_a_retry_grant_is_write_once_per_node_and_key(tmp_path: Path) -> None:
    """One grant buys one attempt, so a doubled command must not mint a second."""
    _co, rd = coordinator(tmp_path)
    key = "v2:sha256:" + "ab" * 32

    rd.write_retry_grant(grant("g_test@1", key))

    with pytest.raises(FileExistsError):
        rd.write_retry_grant(grant("g_test@1", key))


@pytest.mark.os_agnostic
def test_a_second_grant_for_a_later_failure_of_the_same_node_is_a_new_file(tmp_path: Path) -> None:
    """A grant is bound to the KEY, so the attempt it authorises can itself be granted."""
    _co, rd = coordinator(tmp_path)

    rd.write_retry_grant(grant("g_test@1", "v2:sha256:" + "ab" * 32))
    rd.write_retry_grant(grant("g_test@1", "v2:sha256:" + "cd" * 32))

    assert len(rd.retry_grant_files()) == 2


@pytest.mark.os_agnostic
def test_a_retry_grant_refuses_a_node_id_that_could_escape_its_directory(tmp_path: Path) -> None:
    _co, rd = coordinator(tmp_path)

    with pytest.raises(ValueError, match="unsafe node id"):
        rd.write_retry_grant(grant("../../etc/passwd", "v2:sha256:" + "ab" * 32))


@pytest.mark.os_agnostic
def test_a_run_directory_laid_out_before_retries_existed_still_takes_a_grant(tmp_path: Path) -> None:
    """Runs already on disk predate this inbox, so the directory is made on demand."""
    _co, rd = coordinator(tmp_path)
    shutil.rmtree(rd.retries_dir)

    assert rd.retry_grant_files() == []

    rd.write_retry_grant(grant("g_test@1", "v2:sha256:" + "ab" * 32))

    assert len(rd.retry_grant_files()) == 1


@pytest.mark.os_agnostic
def test_folding_journals_every_grant_once_and_a_second_fold_adds_nothing(tmp_path: Path) -> None:
    co, rd = coordinator(tmp_path)
    key = "v2:sha256:" + "ab" * 32
    rd.write_retry_grant(grant("g_test@1", key))

    co.fold_retry_grants()

    assert co.dispatcher.index.grants == {("g_test@1", key)}
    lines = [line for line in co.dispatcher.journal.lines() if line.event == "retry_grant"]
    assert [line.node_id for line in lines] == ["g_test@1"]

    co.fold_retry_grants()

    assert len([line for line in co.dispatcher.journal.lines() if line.event == "retry_grant"]) == 1


@pytest.mark.os_agnostic
def test_scan_does_not_report_the_coordinators_own_inbox_writes_as_a_nodes_stray(tmp_path: Path) -> None:
    """An operator records a grant or a decision while branches are in flight, and those land
    INSIDE the run root that every scan walks. Only ``done/`` of that family was allowed, so a
    write nobody dispatched failed the branch it happened to overlap - and named a coordinator
    file the node never touched.
    """
    co, rd = coordinator(tmp_path)
    (rd.root / "wt/a").mkdir(parents=True)
    before = co.snapshot()

    rd.write_retry_grant(grant("g_test@1", "v2:sha256:" + "ab" * 32))
    rd.write_decision(approved("a", payload()))
    rd.marker("push", "d1", phase=MarkerPhase.ATTEMPTED).touch()

    result = asyncio.run(co.scan(code("g_scan@1", Kind.GATE), watched="w@1", before=before, write_set=["wt/a/**"]))

    assert result.key_facts["stray"] == []
    assert result.status == NodeStatus.DONE


@pytest.mark.os_agnostic
def test_render_for_operator_makes_model_authored_text_askable() -> None:
    """The rules above REFUSE, and that is right when a workflow author wrote the text.

    An exhaustion payload quotes a validator's reasons, which quote what a MODEL wrote, so a
    refusal there would take the run down instead of asking the question it exists to ask.
    Rendering the offending characters as their code points keeps both properties at once:
    what the decider sees is what is hashed, and the payload can be put in front of them.
    """
    hostile = "planner said\x1b[2K stop\u202e reversed\n" + "x" * (MAX_BODY_LINE_OCTETS + 40)

    rendered = render_for_operator(hostile)

    validate_approve_payload(payload(text=rendered))  # raises if any rule still refuses
    assert "\x1b" not in rendered
    assert "U+001B" in rendered
    assert "U+202E" in rendered


@pytest.mark.os_agnostic
def test_render_for_operator_leaves_text_a_person_could_already_read_alone() -> None:
    """The control. Without it a renderer free to mangle everything passes the arm above,
    and every payload's text would stop being the text its author wrote."""
    plain = "the root planner was refused twice:\n  - entry 'x' names unregistered op 'teleport'"

    assert render_for_operator(plain) == plain


@pytest.mark.os_agnostic
def test_render_for_operator_clamps_text_longer_than_a_person_will_read() -> None:
    """A refused plan carries one reason per entry, so a large plan's reasons outrun the
    bound on their own - with no hostile input anywhere in sight."""
    rendered = render_for_operator("reason\n" * MAX_OPERATOR_TEXT_CHARS)

    validate_approve_payload(payload(text=rendered))
    assert len(rendered) <= MAX_OPERATOR_TEXT_CHARS
