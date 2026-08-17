"""RED/GREEN tests for the dispatcher: the ONE path every node takes (design 3.2).

The journal and the run directory are the REAL adapters over ``tmp_path``; only the
clock and the node bodies are fakes, so what these tests assert is what a run leaves
on disk.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.dispatch import Body, Dispatcher
from agentdag.domain.errors import KernelError
from agentdag.domain.journal import StartedLine
from agentdag.domain.models import (
    Budget,
    ErrorType,
    Isolation,
    Kind,
    KnowledgeUsed,
    NodeOutcome,
    NodeSpec,
    NodeStatus,
    Tokens,
)

if TYPE_CHECKING:
    from pathlib import Path


class TickingClock:
    """A clock that advances one second per reading, so every duration is deterministic."""

    def __init__(self) -> None:
        self.reading = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        """Return the next reading, one second after the previous one."""
        self.reading += timedelta(seconds=1)
        return self.reading


def spec(node_id: str, deps: list[str] | None = None) -> NodeSpec:
    """Build the minimal code-node spec these tests dispatch."""
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
    """Build a DONE outcome whose key facts are all typed, and which has an artefact."""
    return NodeOutcome(
        status=NodeStatus.DONE,
        key_facts=dict(facts),
        typed_fields=list(facts),
        executor_used="code",
        model_used="-",
        effort_used="-",
        artefact_refs=["x"],
    )


def make(runs_base: Path) -> tuple[Dispatcher, JsonlJournal, FsRunDir]:
    """Create a fresh run directory under ``runs_base`` and a dispatcher over it."""
    runs_base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(runs_base, "r")
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    return Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=TickingClock()), journal, run_dir


def started_keys(journal: JsonlJournal) -> list[str]:
    """Return every ``started`` key the journal holds, in file order."""
    return [line.key for line in journal.lines() if isinstance(line, StartedLine)]


@pytest.mark.os_agnostic
def test_dispatch_writes_started_then_result_and_the_node_dir_holds_brief_input_and_record(tmp_path: Path) -> None:
    dispatcher, journal, _ = make(tmp_path / "runs")
    seen: list[Path] = []

    async def body(node_dir: Path) -> NodeOutcome:
        seen.append(node_dir)
        return done(n=2)

    record = asyncio.run(dispatcher.dispatch(spec("g"), brief="scan", input_obj={"a": 1}, body=body))

    assert record.status == NodeStatus.DONE
    assert record.key_facts == {"n": 2}
    assert record.duration_s >= 0
    assert [type(line).__name__ for line in journal.lines()] == ["StartedLine", "ResultLine"]
    node_dir = seen[0]
    assert (node_dir / "brief.md").read_text(encoding="utf-8") == "scan"
    assert (node_dir / "input.json").read_text(encoding="utf-8") == '{"a":1}'
    assert (node_dir / "record.json").exists()
    assert dispatcher.dispatched_keys == started_keys(journal)


@pytest.mark.os_agnostic
def test_replay_serves_the_record_without_running_the_body_and_reproduces_the_key_sequence(tmp_path: Path) -> None:
    dispatcher, journal, run_dir = make(tmp_path / "runs")

    async def body(_: Path) -> NodeOutcome:
        return done(n=1)

    async def boom(_: Path) -> NodeOutcome:
        raise AssertionError("body must not run on replay")

    asyncio.run(dispatcher.dispatch(spec("a"), brief="b", input_obj={}, body=body))
    asyncio.run(dispatcher.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=body))

    replay = Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=TickingClock())
    asyncio.run(replay.dispatch(spec("a"), brief="b", input_obj={}, body=boom))
    asyncio.run(replay.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=boom))

    # Replay purity: the same keys, same order, same count. Recording a key only when the
    # body actually runs (instead of before the index check) makes this go red; a UNIFORM
    # key-field change (e.g. dropping the dependency prefix everywhere) does NOT, because
    # this run's keys and the journal's started keys would still move together.
    assert replay.dispatched_keys == started_keys(journal)
    assert len(journal.lines()) == 4  # zero new lines: zero dispatches


@pytest.mark.os_agnostic
def test_a_changed_dep_result_changes_the_dependent_key(tmp_path: Path) -> None:
    async def one(_: Path) -> NodeOutcome:
        return done(n=1)

    async def two(_: Path) -> NodeOutcome:
        return done(n=2)

    first, _, _ = make(tmp_path / "runs")
    asyncio.run(first.dispatch(spec("a"), brief="b", input_obj={}, body=one))
    asyncio.run(first.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=one))

    second, _, _ = make(tmp_path / "other")
    asyncio.run(second.dispatch(spec("a"), brief="b", input_obj={}, body=two))
    asyncio.run(second.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=one))

    # a's key identifies the CALL, so it is the same in both runs even though its result differs;
    # b's key differs, because a's record reaches it through the prefix hash.
    assert first.dispatched_keys[0] == second.dispatched_keys[0]
    assert first.dispatched_keys[1] != second.dispatched_keys[1]


@pytest.mark.os_agnostic
def test_crash_window_is_redispatched_and_only_it(tmp_path: Path) -> None:
    dispatcher, journal, run_dir = make(tmp_path / "runs")
    ran: list[str] = []

    def ok(node_id: str) -> Body:
        async def body(_: Path) -> NodeOutcome:
            ran.append(node_id)
            return done(n=1)

        return body

    async def crash(_: Path) -> NodeOutcome:
        # The coordinator PROCESS dies mid-body; on disk that is a started line with no result.
        # SystemExit is what asyncio propagates straight out of a task and out of asyncio.run,
        # and _run_body catches Exception only, so it is not turned into a tidy failed record.
        raise SystemExit(9)

    async def program(target: Dispatcher, third: Body) -> None:
        await target.dispatch(spec("n1"), brief="b", input_obj={}, body=ok("n1"))
        await target.dispatch(spec("n2", deps=["n1"]), brief="b", input_obj={}, body=ok("n2"))
        await target.dispatch(spec("n3", deps=["n2"]), brief="b", input_obj={}, body=third)

    with pytest.raises(SystemExit):
        asyncio.run(program(dispatcher, crash))

    assert [type(line).__name__ for line in journal.lines()][-1] == "StartedLine"  # n3 started, no result

    crashed_node_dir = next(run_dir.root.glob("nodes/n3/*"))
    assert (crashed_node_dir / "brief.md").exists()  # inputs are written BEFORE the started line
    assert (crashed_node_dir / "input.json").exists()
    assert not (crashed_node_dir / "record.json").exists()  # ... and the crash window is exactly this: no result

    resumed = Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=TickingClock())
    ran.clear()
    asyncio.run(program(resumed, ok("n3")))

    assert ran == ["n3"]  # exactly the crash-window node re-dispatched; n1 and n2 served
    assert resumed.dispatched_keys == started_keys(journal)[:3]


@pytest.mark.os_agnostic
def test_an_empty_or_junk_done_outcome_is_failed_agents_empty_result(tmp_path: Path) -> None:
    dispatcher, _, _ = make(tmp_path / "runs")

    async def empty(_: Path) -> NodeOutcome:
        return NodeOutcome(status=NodeStatus.DONE, executor_used="code", model_used="-", effort_used="-")

    async def junk(_: Path) -> NodeOutcome:
        return NodeOutcome(
            status=NodeStatus.DONE,
            key_facts={"prose": "did it"},
            executor_used="code",
            model_used="-",
            effort_used="-",
        )

    for node_id, body in (("e_empty", empty), ("e_junk", junk)):
        record = asyncio.run(dispatcher.dispatch(spec(node_id), brief="b", input_obj={}, body=body))
        assert record.status == NodeStatus.FAILED
        assert record.error is not None
        assert record.error.type == ErrorType.AGENTS_EMPTY_RESULT
        assert record.error.transient is False


@pytest.mark.os_agnostic
def test_a_dep_dispatched_before_what_it_depends_on_is_a_typed_kernel_error(tmp_path: Path) -> None:
    dispatcher, *_ = make(tmp_path / "runs")

    async def body(_: Path) -> NodeOutcome:
        return done(n=1)

    with pytest.raises(KernelError, match="not dispatched"):
        asyncio.run(dispatcher.dispatch(spec("b", deps=["a"]), brief="b", input_obj={}, body=body))


@pytest.mark.os_agnostic
def test_a_raising_body_is_a_failed_record_not_an_exception(tmp_path: Path) -> None:
    dispatcher, journal, _ = make(tmp_path / "runs")

    async def raising(_: Path) -> NodeOutcome:
        raise RuntimeError("clone failed")

    record = asyncio.run(dispatcher.dispatch(spec("c"), brief="b", input_obj={}, body=raising))

    assert record.status == NodeStatus.FAILED
    assert record.error is not None
    assert record.error.type == ErrorType.EXECUTOR_ERROR
    assert record.error.transient is True
    assert "clone failed" in record.error.message
    assert [type(line).__name__ for line in journal.lines()] == ["StartedLine", "ResultLine"]


@pytest.mark.os_agnostic
def test_a_bodys_measurements_survive_into_the_record_the_file_and_the_journal(tmp_path: Path) -> None:
    dispatcher, journal, run_dir = make(tmp_path / "runs")

    async def measured(_: Path) -> NodeOutcome:
        return NodeOutcome(
            status=NodeStatus.DONE,
            artefact_refs=["tally.json"],
            tokens=Tokens(**{"in": 10, "out": 20, "cache_read": 5, "reasoning": None}),
            charged_tokens={"sonnet": 35},
            knowledge_used=[KnowledgeUsed(dataset="fleet", content_hash="sha256:0")],
            executor_used="claude",
            model_used="sonnet",
            effort_used="-",
        )

    record = asyncio.run(dispatcher.dispatch(spec("m"), brief="b", input_obj={}, body=measured))

    assert record.tokens is not None
    assert record.tokens.in_ == 10
    assert record.charged_tokens == {"sonnet": 35}
    assert [used.dataset for used in record.knowledge_used] == ["fleet"]
    # the nested Tokens keeps its wire alias ("in", not "in_") in both files it reaches
    assert '"in": 10' in next(run_dir.root.glob("nodes/m/*/record.json")).read_text(encoding="utf-8")
    assert '"in":10' in run_dir.journal_path.read_text(encoding="utf-8")
    assert len(journal.lines()) == 2
