"""Tests for one launch of a coordinator: the lock, the bookend lines, the state, the summary.

The runner is exercised over the real graph A workflow and the real adapters (see
``kernel_fakes``); only :func:`~agentdag.application.kernel.summary.run_summary_line`
is tested in isolation, because it is pure and its arithmetic is easier to pin down
against records built by hand than against a run's own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from kernel_fakes import CommittingExecutor, launch

from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.application.kernel.summary import run_summary_line
from agentdag.application.workflows import WORKFLOWS, get_workflow
from agentdag.domain.errors import LockHeld, RunRefused, WorkflowNotFound
from agentdag.domain.journal import RunSummaryLine
from agentdag.domain.models import Decision, NodeStatus, ResultRecord, RunStatus, Tokens

if TYPE_CHECKING:
    from pathlib import Path

    from agentdag.adapters.kernel.run_store_fs import FsRunDir


def record(node_id: str, *, first_turn: int | None = None, in_tokens: int | None = None) -> ResultRecord:
    """Build a done record carrying the two fields the overhead estimate reads."""
    key_facts: dict[str, object] = {} if first_turn is None else {"first_turn_input_tokens": first_turn}
    tokens = None if in_tokens is None else Tokens(**{"in": in_tokens, "out": 0, "cache_read": 0, "reasoning": None})
    return ResultRecord(
        node_id=node_id,
        attempt=0,
        status=NodeStatus.DONE,
        input_hash="sha256:0",
        duration_s=0.0,
        executor_used="code",
        model_used="-",
        effort_used="-",
        key_facts=key_facts,
        typed_fields=list(key_facts),
        tokens=tokens,
    )


def approve_and_resume(tmp_path: Path, executor: CommittingExecutor) -> FsRunDir:
    """Run graph A to its suspend, record an approval, and resume it to done."""
    _, run_dir = launch(tmp_path, executor)
    run_dir.write_decision(Decision(node_id="a_push_list", decision="approve", by="tester", token_id="local"))
    launch(tmp_path, executor, resume="decision")
    return run_dir


@pytest.mark.os_agnostic
def test_a_second_coordinator_on_a_live_locked_run_dir_is_refused(tmp_path: Path) -> None:
    executor = CommittingExecutor()
    _, run_dir = launch(tmp_path, executor)
    lock = FileRunLock()
    token = lock.acquire(run_dir.root, current_holder())

    try:
        with pytest.raises(LockHeld):
            launch(tmp_path, executor, resume="manual")
    finally:
        lock.release(token)


@pytest.mark.os_agnostic
def test_a_done_run_ends_with_a_summary_line_carrying_the_interactions_and_the_tokens(tmp_path: Path) -> None:
    run_dir = approve_and_resume(tmp_path, CommittingExecutor())

    lines = JsonlJournal(run_dir.journal_path, run_dir.audit_path).lines()
    summary = lines[-1]
    assert isinstance(summary, RunSummaryLine)
    assert summary.run_id == "r1"
    assert summary.human_interactions == 1
    assert summary.tokens_by_row == {"sonnet": 30}
    assert summary.journal_lines == len(lines) - 1  # measured before this line was appended
    assert summary.journal_bytes > 0
    assert summary.replay_seconds is not None  # this launch replayed a journal
    assert summary.records_per_node == 1.0
    assert summary.citation_coverage == []


@pytest.mark.os_agnostic
def test_the_state_file_carries_the_coordinator_s_own_token_totals_not_a_running_sum(tmp_path: Path) -> None:
    run_dir = approve_and_resume(tmp_path, CommittingExecutor())

    state = run_dir.read_state()
    # The resume charged the same two work records again, from the journal. 60 here would
    # mean the launch ADDED its totals to the file's; 30 is the coordinator's own count.
    assert state.tokens_by_row == {"sonnet": 30}
    assert state.status == RunStatus.DONE
    assert state.cursor is None
    assert state.owner == "tester"


@pytest.mark.os_agnostic
def test_an_empty_fleet_halts_at_discover_and_the_first_launch_is_already_done(tmp_path: Path) -> None:
    outcome, run_dir = launch(tmp_path, CommittingExecutor(), names=[])

    assert outcome.status == RunStatus.DONE
    assert outcome.suspended_node is None
    assert len(outcome.dispatched_keys) == 1  # g_discover, and nothing after it
    summary = JsonlJournal(run_dir.journal_path, run_dir.audit_path).lines()[-1]
    assert isinstance(summary, RunSummaryLine)
    assert summary.replay_seconds is None  # a first start replays nothing


@pytest.mark.os_agnostic
def test_an_unknown_resume_reason_is_refused(tmp_path: Path) -> None:
    executor = CommittingExecutor()
    launch(tmp_path, executor)

    with pytest.raises(RunRefused):
        launch(tmp_path, executor, resume="whenever")


@pytest.mark.os_agnostic
def test_get_workflow_refuses_a_name_it_does_not_have() -> None:
    assert get_workflow("graph-a").module.__name__.endswith("graph_a")
    assert set(WORKFLOWS) == {"graph-a"}

    with pytest.raises(WorkflowNotFound):
        get_workflow("graph-z")


@pytest.mark.os_agnostic
def test_run_summary_line_reports_zeros_when_no_record_carries_a_first_turn() -> None:
    line = run_summary_line(
        run_id="r1",
        policy_version="sha256:0",
        records=[record("a"), record("b", first_turn=10)],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=None,
        human_interactions=0,
        tokens_by_row={},
        at="2026-08-17T09:12:03+00:00",
        brief_lengths={},
    )

    assert line.overhead_fraction == {"median": 0.0, "p90": 0.0}
    assert line.records_per_node == 1.0


@pytest.mark.os_agnostic
def test_run_summary_line_estimates_the_brief_s_share_out_of_the_first_turn() -> None:
    line = run_summary_line(
        run_id="r1",
        policy_version="sha256:0",
        records=[
            record("a", first_turn=200, in_tokens=400),  # no brief: all 200 is overhead -> 0.5
            record("b", first_turn=400, in_tokens=400),  # 400 chars ~ 100 tokens of brief -> 0.75
            record("c", first_turn=999, in_tokens=0),  # no input tokens to divide by: excluded
        ],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=0.25,
        human_interactions=2,
        tokens_by_row={"sonnet": 5},
        at="2026-08-17T09:12:03+00:00",
        brief_lengths={"b": 400},
    )

    assert line.overhead_fraction == {"median": 0.625, "p90": 0.75}
    assert line.replay_seconds == 0.25


@pytest.mark.os_agnostic
def test_run_summary_line_counts_a_redispatched_node_as_drift() -> None:
    line = run_summary_line(
        run_id="r1",
        policy_version="sha256:0",
        records=[record("a"), record("a"), record("b")],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=None,
        human_interactions=0,
        tokens_by_row={},
        at="2026-08-17T09:12:03+00:00",
        brief_lengths={},
    )

    assert line.records_per_node == 1.5
