"""Tests for one launch of a coordinator: the lock, the bookend lines, the state, the summary.

The runner is exercised over the real graph A workflow and the real adapters (see
``kernel_fakes``); only :func:`~agentdag.application.kernel.summary.run_summary_line`
is tested in isolation, because it is pure and its arithmetic is easier to pin down
against records built by hand than against a run's own.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from kernel_fakes import CommittingExecutor, decide, launch, policy_path
from pydantic import BaseModel
from schema_helpers import validator

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.kernel.summary import run_summary_line
from agentdag.application.workflows import WORKFLOWS, get_workflow
from agentdag.domain.journal import ResultLine, RunSummaryLine, dump_journal_line, parse_journal_line
from agentdag.domain.kernel_errors import LockHeld, RunRefused, WorkflowNotFound
from agentdag.domain.models import NodeStatus, ResultRecord, RetryGrant, RunStatus, Tokens

if TYPE_CHECKING:
    from pathlib import Path


AT = "2026-08-17T09:12:03+00:00"


def result(
    node_id: str, *, key: str | None = None, first_turn: int | None = None, in_tokens: int | None = None
) -> ResultLine:
    """Build a done result LINE: the key the brief length is joined on, plus the record."""
    key_facts: dict[str, object] = {} if first_turn is None else {"first_turn_input_tokens": first_turn}
    tokens = None if in_tokens is None else Tokens(**{"in": in_tokens, "out": 0, "cache_read": 0, "reasoning": None})
    record = ResultRecord(
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
    return ResultLine(key=key if key is not None else f"key-{node_id}", record=record, at=AT)


def approve_and_resume(tmp_path: Path, executor: CommittingExecutor) -> FsRunDir:
    """Run graph A to its suspend, record an approval, and resume it to done."""
    _, run_dir = launch(tmp_path, executor)
    decide(run_dir, "approve")
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
def test_every_journal_line_of_a_real_run_validates_against_the_journal_line_schema(tmp_path: Path) -> None:
    # test_kernel_schemas.py proves the schema's OWN hand-picked examples validate; this
    # proves a REAL run's actual output does too - run_started, started, result,
    # approve_decision, resume and run_summary lines, exactly as the kernel wrote them.
    run_dir = approve_and_resume(tmp_path, CommittingExecutor())

    v = validator("journal-line")
    raw_lines = run_dir.journal_path.read_text(encoding="utf-8").splitlines()
    assert raw_lines  # a run that logged nothing would pass this vacuously
    for raw in raw_lines:
        v.validate(json.loads(dump_journal_line(parse_journal_line(raw))))


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
        results=[result("a", in_tokens=400), result("b", in_tokens=400)],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=None,
        human_interactions=0,
        tokens_by_row={},
        at=AT,
        brief_lengths={},
    )

    assert line.overhead_fraction == {"median": 0.0, "p90": 0.0}
    assert line.records_per_node == 1.0


@pytest.mark.os_agnostic
def test_run_summary_line_ignores_a_first_turn_with_no_input_tokens_to_divide_by() -> None:
    # Separate from the test above on purpose: a first turn with no token count and no first turn
    # at all are two different exclusions, and one test covering both passes for either reason.
    line = run_summary_line(
        run_id="r1",
        policy_version="sha256:0",
        results=[result("a", first_turn=10), result("b", first_turn=10, in_tokens=0)],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=None,
        human_interactions=0,
        tokens_by_row={},
        at=AT,
        brief_lengths={},
    )

    assert line.overhead_fraction == {"median": 0.0, "p90": 0.0}


@pytest.mark.os_agnostic
def test_run_summary_line_estimates_the_brief_s_share_out_of_the_first_turn() -> None:
    line = run_summary_line(
        run_id="r1",
        policy_version="sha256:0",
        results=[
            result("a", first_turn=200, in_tokens=400),  # no brief: all 200 is overhead -> 0.5
            result("b", first_turn=400, in_tokens=400),  # 400 chars ~ 100 tokens of brief -> 0.75
            result("c", first_turn=999, in_tokens=0),  # no input tokens to divide by: excluded
        ],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=0.25,
        human_interactions=2,
        tokens_by_row={"sonnet": 5},
        at=AT,
        brief_lengths={"key-b": 400},
    )

    assert line.overhead_fraction == {"median": 0.625, "p90": 0.75}
    assert line.replay_seconds == 0.25


@pytest.mark.os_agnostic
def test_run_summary_line_clamps_overhead_at_one_when_the_first_turn_exceeds_the_input_tokens() -> None:
    # Defensive: first_turn_input_tokens should never exceed tokens.in, but nothing upstream
    # enforces that, and an unclamped ratio would report more than "all of it was overhead".
    line = run_summary_line(
        run_id="r1",
        policy_version="sha256:0",
        results=[result("a", first_turn=500, in_tokens=100)],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=None,
        human_interactions=0,
        tokens_by_row={},
        at=AT,
        brief_lengths={},
    )

    assert line.overhead_fraction == {"median": 1.0, "p90": 1.0}


@pytest.mark.os_agnostic
def test_run_summary_line_counts_a_redispatched_node_as_drift() -> None:
    line = run_summary_line(
        run_id="r1",
        policy_version="sha256:0",
        results=[result("a", key="k1"), result("a", key="k2"), result("b")],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=None,
        human_interactions=0,
        tokens_by_row={},
        at=AT,
        brief_lengths={},
    )

    assert line.records_per_node == 1.5


@pytest.mark.os_agnostic
def test_run_summary_line_measures_each_dispatch_against_its_own_brief() -> None:
    # The same node, dispatched twice under different briefs. Attributing one brief to both
    # records - which keying by node id would do - reports 0.1 twice and hides the 0.55.
    line = run_summary_line(
        run_id="r1",
        policy_version="sha256:0",
        results=[
            result("w", key="long", first_turn=1200, in_tokens=2000),
            result("w", key="short", first_turn=1200, in_tokens=2000),
        ],
        journal_bytes=1,
        journal_lines=1,
        replay_seconds=None,
        human_interactions=0,
        tokens_by_row={},
        at=AT,
        brief_lengths={"long": 4000, "short": 400},
    )

    assert line.overhead_fraction == {"median": 0.325, "p90": 0.55}


@pytest.mark.os_agnostic
def test_run_coordinator_refuses_arguments_that_are_not_the_workflow_s_own(tmp_path: Path) -> None:
    class OtherArgs(BaseModel):
        """Some other workflow's arguments."""

        whatever: int = 1

    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "r1")

    with pytest.raises(RunRefused, match="GraphAArgs"):
        asyncio.run(
            run_coordinator(
                run_dir=run_dir,
                journal=JsonlJournal(run_dir.journal_path, run_dir.audit_path),
                clock=UtcClock(),
                lock=FileRunLock(),
                holder=current_holder(),
                workflow=get_workflow("graph-a"),
                args=OtherArgs(),
                executors={"claude": CommittingExecutor()},
                gate_port=MakeTestGate(),
                git=GitCli(),
                scanner=IsolationScanner(),
                policy=load_policy(policy_path()),
                sandbox=NoSandbox(),
                parallel=1,
                by="tester",
                token_id="local",
                resume_reason=None,
                notifier=NoNotifier(),
            )
        )

    # Refused before the lock left anything behind, before the journal was opened, and before
    # state.json claimed a run was running.
    assert not run_dir.journal_path.exists()
    assert not run_dir.state_path.exists()
    assert not (run_dir.root / "lock").exists()


@pytest.mark.os_agnostic
def test_a_launch_folds_the_retry_grants_an_operator_recorded_while_nothing_was_running(tmp_path: Path) -> None:
    """The fold has to be WIRED, not merely to exist: an operator records a grant with no
    coordinator running, so the launch that acts on it is the one that must journal it -
    before anything dispatches, so it is already in the index when the failure is served back.
    """
    executor = CommittingExecutor()
    _, run_dir = launch(tmp_path, executor)
    granted_key = "v2:sha256:" + "ab" * 32
    run_dir.write_retry_grant(
        RetryGrant(node_id="g_test@0", key=granted_key, reason="fixed by hand", by="me", token_id="local")
    )

    launch(tmp_path, executor, resume="retry")

    lines = JsonlJournal(run_dir.journal_path, run_dir.audit_path).lines()
    grants = [line for line in lines if line.event == "retry_grant"]
    assert [line.key for line in grants] == [granted_key]
    assert [line.reason for line in lines if line.event == "resume"] == ["retry"]
