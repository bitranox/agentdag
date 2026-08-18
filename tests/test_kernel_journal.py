"""RED/GREEN tests for the JSONL journal adapter and the pure replay index built from it."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.application.kernel.replay import build_replay_index
from agentdag.domain.journal import ApproveDecisionLine, ResultLine, RunStartedLine, StartedLine
from agentdag.domain.models import NodeStatus, ResultRecord

if TYPE_CHECKING:
    from pathlib import Path

AT = "2026-08-17T09:12:03+00:00"


def rec(node_id: str) -> ResultRecord:
    """Build a minimal DONE result record for ``node_id``."""
    return ResultRecord(
        node_id=node_id,
        attempt=0,
        status=NodeStatus.DONE,
        executor_used="code",
        model_used="-",
        effort_used="-",
        input_hash="sha256:0",
        duration_s=0.0,
    )


def key(n: int) -> str:
    """Build a syntactically valid journal key from a small integer, for readable tests."""
    return "v2:sha256:" + f"{n:064x}"


@pytest.mark.os_agnostic
def test_journal_appends_one_line_per_call_and_replays_them_typed(tmp_path: Path) -> None:
    j = JsonlJournal(tmp_path / "journal.jsonl", tmp_path / "audit.jsonl")
    j.append(
        RunStartedLine(run_id="r", workflow="w", args={}, by="me", token_id="local", policy_version="sha256:p", at=AT)
    )
    j.append(StartedLine(key=key(1), node_id="a", attempt=0, at=AT))
    j.append(ResultLine(key=key(1), record=rec("a"), at=AT))
    j.append(StartedLine(key=key(2), node_id="b", attempt=0, at=AT))

    journal_text = (tmp_path / "journal.jsonl").read_text()
    assert journal_text.count("\n") == 4
    assert (tmp_path / "audit.jsonl").read_text() == journal_text

    idx = build_replay_index(j.lines())
    assert set(idx.results) == {key(1)}
    assert idx.crash_window == {key(2)}
    assert idx.key_sequence == [key(1), key(2)]
    assert idx.run_started is not None
    assert idx.run_started.run_id == "r"


@pytest.mark.os_agnostic
def test_replay_index_keeps_the_latest_decision_per_node_and_payload_and_the_result_of_a_repeated_key() -> None:
    # One node, two payloads: two independent decisions, not an overwrite. A line written before
    # payload_hash existed keys as (node_id, "") and stays reachable under that pair.
    lines = [
        ApproveDecisionLine(node_id="a_push_list", decision="hold", reason="", by="me", token_id="local", at=AT),
        ApproveDecisionLine(
            node_id="a_push_list",
            decision="approve",
            reason="",
            by="me",
            token_id="local",
            payload_hash="sha256:aa",
            at=AT,
        ),
        StartedLine(key=key(3), node_id="c", attempt=0, at=AT),
        StartedLine(key=key(3), node_id="c", attempt=0, at=AT),
        ResultLine(key=key(3), record=rec("c"), at=AT),
    ]

    idx = build_replay_index(lines)

    assert idx.decisions["a_push_list", ""].decision == "hold"
    assert idx.decisions["a_push_list", "sha256:aa"].decision == "approve"
    assert key(3) in idx.results
    assert idx.crash_window == set()
    # every started is a dispatch attempt; the sequence is the replay-purity oracle
    assert idx.key_sequence == [key(3), key(3)]


@pytest.mark.os_agnostic
def test_journal_files_are_owner_only_and_a_torn_last_line_is_reported_not_swallowed(tmp_path: Path) -> None:
    j = JsonlJournal(tmp_path / "journal.jsonl", tmp_path / "audit.jsonl")
    j.append(StartedLine(key=key(1), node_id="a", attempt=0, at=AT))

    if sys.platform != "win32":
        assert (tmp_path / "journal.jsonl").stat().st_mode & 0o777 == 0o600

    with (tmp_path / "journal.jsonl").open("a") as handle:
        handle.write('{"event": "started", "key": "v2:s')

    with pytest.raises(ValueError, match="line 2"):
        j.lines()
