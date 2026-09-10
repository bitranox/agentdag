"""Tests for the SlopCodeBench control-arm readings extractor.

The two cases that matter are the ones that fail SILENTLY in production: a cumulative ``result``
event counted as a request, and ``checkpoint_10`` sorting beside ``checkpoint_1``. Both produce a
plausible number rather than an error, so each is pinned here rather than checked by eye.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from slopcodebench_readings import collect_problem, peak_prompt_tokens, read_checkpoint

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

pytestmark = pytest.mark.os_agnostic


def _group(*, failed: int = 0, passed: int = 0) -> dict[str, list[str]]:
    """One test group in the harness's shape: named tests under ``passed`` and ``failed``."""
    return {
        "passed": [f"passed_{i}" for i in range(passed)],
        "failed": [f"failed_{i}" for i in range(failed)],
    }


def _write_checkpoint(
    root: Path,
    name: str,
    *,
    pass_counts: dict[str, int],
    total_counts: dict[str, int],
    usage_lines: Sequence[Mapping[str, object]] | None = None,
    tests: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    steps: int = 3,
) -> Path:
    checkpoint = root / name
    (checkpoint / "agent").mkdir(parents=True)
    (checkpoint / "evaluation.json").write_text(
        json.dumps(
            {
                "pass_counts": pass_counts,
                "total_counts": total_counts,
                "pytest_collected": sum(total_counts.values()),
                "infrastructure_failure": False,
                "tests": tests if tests is not None else {},
            }
        )
    )
    (checkpoint / "inference_result.json").write_text(
        json.dumps(
            {
                "elapsed": 12.5,
                "had_error": False,
                "usage": {
                    "cost": 1.5,
                    "steps": steps,
                    "current_tokens": {"input": 100, "cache_write": 200, "cache_read": 9999},
                },
            }
        )
    )
    if usage_lines is not None:
        (checkpoint / "agent" / "stdout.jsonl").write_text("\n".join(json.dumps(line) for line in usage_lines))
    return checkpoint


def test_regression_tests_separate_strict_from_isolated(tmp_path: Path) -> None:
    """A failed regression test must drag strict down while leaving isolated at 1.0."""
    checkpoint = _write_checkpoint(
        tmp_path,
        "checkpoint_2",
        pass_counts={"Core": 2, "Functionality": 3, "Regression": 0},
        total_counts={"Core": 2, "Functionality": 3, "Regression": 5},
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.strict_pass_rate == pytest.approx(5 / 10)
    assert reading.isolated_pass_rate == pytest.approx(1.0)
    assert reading.core_pass_rate == pytest.approx(1.0)


def test_peak_excludes_the_cumulative_result_event(tmp_path: Path) -> None:
    """The ``result`` event holds the whole dispatch's totals and is not a request."""
    checkpoint = _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 1},
        total_counts={"Core": 1},
        usage_lines=[
            {
                "type": "assistant",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 40_000,
                    "cache_creation_input_tokens": 1_000,
                },
            },
            {
                "type": "result",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 900_000,
                    "cache_creation_input_tokens": 1_000,
                },
            },
        ],
    )
    assert peak_prompt_tokens(checkpoint / "agent" / "stdout.jsonl") == 41_010


def test_a_repeated_message_id_does_not_raise_the_peak(tmp_path: Path) -> None:
    """The CLI repeats one message's usage per content block; a max must be unmoved by it."""
    block = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "usage": {
                "input_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 20,
            },
        },
    }
    checkpoint = _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 1},
        total_counts={"Core": 1},
        usage_lines=[block, block, block],
    )
    assert peak_prompt_tokens(checkpoint / "agent" / "stdout.jsonl") == 125


def test_checkpoint_10_sorts_after_checkpoint_9(tmp_path: Path) -> None:
    """Lexical ordering would put checkpoint_10 second and silently reorder the curve."""
    for name in ("checkpoint_1", "checkpoint_9", "checkpoint_10"):
        _write_checkpoint(tmp_path, name, pass_counts={"Core": 1}, total_counts={"Core": 1}, usage_lines=[])
    problem = collect_problem(tmp_path)
    assert [c.checkpoint for c in problem.checkpoints] == [
        "checkpoint_1",
        "checkpoint_9",
        "checkpoint_10",
    ]


def test_an_unevaluated_checkpoint_is_skipped_not_scored_zero(tmp_path: Path) -> None:
    """A checkpoint still running has no evaluation.json, and 0.0 would be a false reading."""
    (tmp_path / "checkpoint_1").mkdir()
    assert read_checkpoint(tmp_path / "checkpoint_1", problem="p") is None


def test_solved_counts_only_a_full_strict_pass(tmp_path: Path) -> None:
    """``S`` is the pre-registered count of checkpoints at strict_pass_rate exactly 1.0."""
    _write_checkpoint(tmp_path, "checkpoint_1", pass_counts={"Core": 2}, total_counts={"Core": 2}, usage_lines=[])
    _write_checkpoint(tmp_path, "checkpoint_2", pass_counts={"Core": 1}, total_counts={"Core": 2}, usage_lines=[])
    assert collect_problem(tmp_path).solved == 1


def test_a_checkpoint_with_no_core_tests_is_excluded_from_mean_core_not_scored_zero(tmp_path: Path) -> None:
    """0/0 Core scores 0.0 from the harness formula, which would drag the pre-registered C down."""
    _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 2, "Functionality": 1},
        total_counts={"Core": 2, "Functionality": 1},
        usage_lines=[],
    )
    _write_checkpoint(
        tmp_path,
        "checkpoint_2",
        pass_counts={"Functionality": 1},
        total_counts={"Functionality": 1},
        usage_lines=[],
    )
    problem = collect_problem(tmp_path)
    assert [c.core_total for c in problem.checkpoints] == [2, 0]
    # Averaging the vacuous 0.0 in would give 0.5; excluding it gives the true 1.0.
    assert problem.mean_core_pass_rate == pytest.approx(1.0)


def test_mean_core_is_none_when_no_checkpoint_has_core_tests(tmp_path: Path) -> None:
    """None is reportable as 'not measured'; 0.0 would read as total failure."""
    _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Functionality": 1},
        total_counts={"Functionality": 1},
        usage_lines=[],
    )
    assert collect_problem(tmp_path).mean_core_pass_rate is None


def test_a_carried_defect_that_survives_reads_zero_repaired(tmp_path: Path) -> None:
    """The identity case: every inherited failure fails again, so nothing was repaired."""
    _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 0},
        total_counts={"Core": 2},
        tests={"checkpoint_1-Core": _group(failed=2)},
    )
    _write_checkpoint(
        tmp_path,
        "checkpoint_2",
        pass_counts={"Core": 0, "Regression": 0},
        total_counts={"Core": 1, "Regression": 2},
        tests={"checkpoint_1-Regression": _group(failed=2), "checkpoint_2-Core": _group(failed=1)},
    )
    second = collect_problem(tmp_path).checkpoints[1]
    assert second.repaired == 0
    assert (second.failed_own, second.failed_inherited) == (1, 2)


def test_a_cleared_inherited_failure_counts_as_one_repair(tmp_path: Path) -> None:
    """One of the two carried defects passes at checkpoint 2, so the repair count is 1.

    Checkpoint 2 fails two tests of its OWN as well, so that this reading also pins the origin
    split. Swapping own for inherited moves the result by ``inherited - own`` at THIS checkpoint
    (the previous checkpoint's two counts are summed, so re-partitioning them cancels), which is
    -1 here. A fixture with no own failures would be caught too, at +1; the one shape that hides
    the swap is own and inherited being equal.
    """
    _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 0},
        total_counts={"Core": 2},
        tests={"checkpoint_1-Core": _group(failed=2)},
    )
    _write_checkpoint(
        tmp_path,
        "checkpoint_2",
        pass_counts={"Core": 0, "Regression": 1},
        total_counts={"Core": 2, "Regression": 2},
        tests={
            "checkpoint_1-Regression": _group(failed=1, passed=1),
            "checkpoint_2-Core": _group(failed=2),
        },
    )
    second = collect_problem(tmp_path).checkpoints[1]
    assert second.repaired == 1
    assert (second.failed_own, second.failed_inherited) == (2, 1)


def test_a_checkpoint_with_no_regression_suite_reads_none_not_zero(tmp_path: Path) -> None:
    """With no regression suite the zero inherited failures are vacuous, not a measured repair."""
    _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 0},
        total_counts={"Core": 2},
        tests={"checkpoint_1-Core": _group(failed=2)},
    )
    _write_checkpoint(
        tmp_path,
        "checkpoint_2",
        pass_counts={"Core": 1},
        total_counts={"Core": 1},
        tests={"checkpoint_2-Core": _group(failed=0, passed=1)},
    )
    second = collect_problem(tmp_path).checkpoints[1]
    assert second.repaired is None
    assert second.failed_inherited == 0


def test_the_first_checkpoint_of_a_problem_has_no_repair_reading(tmp_path: Path) -> None:
    """Nothing was carried into the first checkpoint, so there is nothing to have repaired.

    It is given a regression suite so that the ``None`` is owed to the absent predecessor alone,
    not to the no-regression-suite rule that a real checkpoint 1 would also satisfy.
    """
    _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 0, "Regression": 3},
        total_counts={"Core": 1, "Regression": 3},
        tests={
            "checkpoint_1-Core": _group(failed=1),
            "checkpoint_1-Regression": _group(passed=3),
        },
    )
    first = collect_problem(tmp_path).checkpoints[0]
    assert first.repaired is None
    assert first.regression_total == 3


def test_repaired_total_and_defined_fold_over_the_problem(tmp_path: Path) -> None:
    """The problem's totals count only the checkpoints where a repair could be observed."""
    _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 0},
        total_counts={"Core": 2},
        tests={"checkpoint_1-Core": _group(failed=2)},
    )
    _write_checkpoint(
        tmp_path,
        "checkpoint_2",
        pass_counts={"Core": 0, "Regression": 1},
        total_counts={"Core": 1, "Regression": 2},
        tests={
            "checkpoint_1-Regression": _group(failed=1, passed=1),
            "checkpoint_2-Core": _group(failed=1),
        },
    )
    _write_checkpoint(
        tmp_path,
        "checkpoint_3",
        pass_counts={"Core": 1, "Regression": 1},
        total_counts={"Core": 1, "Regression": 3},
        tests={
            "checkpoint_1-Regression": _group(failed=1),
            "checkpoint_2-Regression": _group(failed=1, passed=1),
            "checkpoint_3-Core": _group(failed=0, passed=1),
        },
    )
    problem = collect_problem(tmp_path)
    assert [c.repaired for c in problem.checkpoints] == [None, 1, 0]
    assert problem.repaired_total == 1
    assert problem.repaired_defined == 2


def test_a_checkpoint_after_an_unevaluated_gap_has_no_repair_reading(tmp_path: Path) -> None:
    """A skipped checkpoint breaks the chain, and the difference across the gap is not a repair.

    Checkpoint 3 inherited its failures from checkpoint 2, which was dispatched and never scored.
    What checkpoint 1 was failing says nothing about what checkpoint 3 cleared, so subtracting
    the two would report a repair count for work nobody measured.
    """
    _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 0},
        total_counts={"Core": 2},
        tests={"checkpoint_1-Core": _group(failed=2)},
    )
    (tmp_path / "checkpoint_2").mkdir()
    _write_checkpoint(
        tmp_path,
        "checkpoint_3",
        pass_counts={"Core": 0, "Regression": 1},
        total_counts={"Core": 1, "Regression": 2},
        tests={
            "checkpoint_1-Regression": _group(failed=1, passed=1),
            "checkpoint_3-Core": _group(failed=1),
        },
    )
    checkpoints = collect_problem(tmp_path).checkpoints
    assert [c.checkpoint for c in checkpoints] == ["checkpoint_1", "checkpoint_3"]
    assert checkpoints[1].repaired is None


def _message(msg_id: str, *, input_tokens: int, cache_write: int) -> dict[str, object]:
    """One assistant event in the CLI's shape, keyed by its message id."""
    return {
        "type": "assistant",
        "message": {
            "id": msg_id,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": 1_000,
                "cache_creation_input_tokens": cache_write,
            },
        },
    }


_INIT: dict[str, object] = {"type": "system", "subtype": "init"}
_RESULT: dict[str, object] = {"type": "result", "subtype": "success", "num_turns": 1}
_ORPHANED: dict[str, object] = {
    "type": "system",
    "subtype": "task_notification",
    "status": "stopped",
    "summary": "Orphaned by a previous Claude Code process exit and reported in an aggregate summary.",
}


def test_new_tokens_are_summed_from_the_stream_not_the_last_result(tmp_path: Path) -> None:
    """The harness records only the LAST result event's usage as the checkpoint's.

    A background task waking the model emits a fresh result, so a checkpoint with several
    result events reads a single wake-up's tokens off the record (measured: 409 against a
    stream summing to 180,760). The protocol's unit is the sum over the checkpoint.
    """
    checkpoint = _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 1},
        total_counts={"Core": 1},
        usage_lines=[
            _INIT,
            _message("msg_1", input_tokens=10, cache_write=1_000),
            _RESULT,
            _INIT,
            _message("msg_2", input_tokens=20, cache_write=2_000),
            _RESULT,
        ],
        steps=2,
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.new_tokens == 3_030
    assert reading.result_events == 2
    assert reading.bound_hit is False


def test_a_repeated_message_id_is_charged_once_in_new_tokens(tmp_path: Path) -> None:
    """The CLI repeats one message's usage per content block; the sum must not."""
    block = _message("msg_1", input_tokens=5, cache_write=20)
    checkpoint = _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 1},
        total_counts={"Core": 1},
        usage_lines=[block, block, block],
        steps=1,
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.new_tokens == 25


def test_an_orphaned_task_marks_the_turn_bound_hit(tmp_path: Path) -> None:
    """A process exit that strands a background task is the bound hit with work in flight."""
    checkpoint = _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 1},
        total_counts={"Core": 1},
        usage_lines=[_ORPHANED, _INIT, _message("msg_1", input_tokens=1, cache_write=1), _RESULT],
        steps=1,
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.orphaned_tasks == 1
    assert reading.bound_hit is True


def test_steps_missing_from_the_stream_mark_the_turn_bound_hit(tmp_path: Path) -> None:
    """A harness retry replaces the stream, so its step count outruns the messages left in it."""
    checkpoint = _write_checkpoint(
        tmp_path,
        "checkpoint_1",
        pass_counts={"Core": 1},
        total_counts={"Core": 1},
        usage_lines=[_INIT, _message("msg_1", input_tokens=1, cache_write=1), _RESULT],
        steps=166,
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.steps_missing_from_stream == 165
    assert reading.bound_hit is True


# --- Coordinator checkpoints -------------------------------------------------------------
#
# A coordinator checkpoint has no `agent/stdout.jsonl`: it saves `agent/stdout.log` plus one
# `agent/runs/<run id>/` holding the coordinator's journal and a `transcript.jsonl` per node
# DISPATCH. Those transcripts are the Agent SDK's own message objects (`AssistantMessage`,
# `ResultMessage`), not the CLI's stream, so the control's fold mis-parses them: it excludes a
# cumulative result by `type == "result"` and the SDK writes `"ResultMessage"`, which fed a
# whole dispatch's total into a PER-REQUEST peak. Measured on the real parity run before this
# was fixed: peak 1,874,489 against a 200,000 context window, and every coordinator checkpoint
# reported VOID because `steps_missing_from_stream` equalled its entire step count.


def _sdk_assistant(msg_id: str, *, input_tokens: int, cache_read: int, cache_write: int) -> dict[str, object]:
    """One Agent SDK `AssistantMessage`, whose usage is that REQUEST's own."""
    return {
        "type": "AssistantMessage",
        "message_id": msg_id,
        "model": "claude-opus-5",
        "usage": {
            "input_tokens": input_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
            "output_tokens": 11,
        },
    }


def _sdk_result(*, input_tokens: int, cache_read: int, cache_write: int) -> dict[str, object]:
    """One Agent SDK `ResultMessage`, whose usage is the whole DISPATCH's cumulative total."""
    return {
        "type": "ResultMessage",
        "subtype": "success",
        "num_turns": 3,
        "total_cost_usd": 0.5,
        "usage": {
            "input_tokens": input_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
            "output_tokens": 22,
        },
    }


def _write_coordinator(
    checkpoint: Path,
    *,
    dispatches: Mapping[str, Sequence[Mapping[str, object]]],
    records: Sequence[Mapping[str, object]] = (),
) -> None:
    """Give a checkpoint the artifacts a coordinator run leaves, replacing any control stream."""
    stdout = checkpoint / "agent" / "stdout.jsonl"
    if stdout.exists():
        stdout.unlink()
    (checkpoint / "agent" / "stdout.log").write_text("run 20260910T000000Z-aaaaaa done\n")
    run = checkpoint / "agent" / "runs" / "20260910T000000Z-aaaaaa"
    run.mkdir(parents=True)
    journal = [{"event": "run_started"}, *records]
    (run / "journal.jsonl").write_text("\n".join(json.dumps(line) for line in journal))
    for node_dispatch, lines in dispatches.items():
        node, dispatch = node_dispatch.split("/")
        transcript = run / "nodes" / node / dispatch
        transcript.mkdir(parents=True)
        (transcript / "transcript.jsonl").write_text("\n".join(json.dumps(line) for line in lines))


def _record(node_id: str, *, error_type: str | None = None) -> dict[str, object]:
    """One journal `result` line, optionally carrying a node error."""
    record: dict[str, object] = {"node_id": node_id, "status": "done"}
    if error_type is not None:
        record["error"] = {"type": error_type, "message": "x", "transient": False}
        record["status"] = "refused"
    return {"event": "result", "record": record}


def test_a_coordinator_checkpoint_is_not_void_merely_for_having_no_cli_stream(tmp_path: Path) -> None:
    """The defect this closes: EVERY coordinator checkpoint read VOID, so no arm could be tallied.

    `steps_missing_from_stream` compares harness steps against messages left in the agent's CLI
    stream, and a coordinator has none - its steps are counted from its NODES' transcripts - so
    the difference was the whole step count on every coordinator run.
    """
    checkpoint = _write_checkpoint(
        tmp_path, "checkpoint_1", pass_counts={"Core": 2}, total_counts={"Core": 2}, steps=173
    )
    _write_coordinator(
        checkpoint,
        dispatches={
            "p_root/aaaa": [
                _sdk_assistant("m1", input_tokens=5, cache_read=100, cache_write=50),
                _sdk_result(input_tokens=5, cache_read=100, cache_write=50),
            ]
        },
        records=[_record("p_root")],
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.bound_hit is False


def test_a_coordinator_node_whose_continuation_chain_is_exhausted_is_void(tmp_path: Path) -> None:
    """The carve-out the pre-registration DOES name: handing over is normal, running out is not."""
    checkpoint = _write_checkpoint(tmp_path, "checkpoint_1", pass_counts={"Core": 2}, total_counts={"Core": 2})
    _write_coordinator(
        checkpoint,
        dispatches={"p_root/aaaa": [_sdk_result(input_tokens=5, cache_read=100, cache_write=50)]},
        records=[_record("p_root"), _record("n-0001", error_type="continuation_limit")],
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.bound_hit is True


def test_a_coordinator_s_new_tokens_come_from_each_dispatch_s_cumulative_result(tmp_path: Path) -> None:
    """Summed per DISPATCH from its `ResultMessage`, which is that dispatch's own total.

    Proved against the real parity run: this fold reproduces the harness's independently recorded
    input + cache_write to the token (366,711).
    """
    checkpoint = _write_checkpoint(tmp_path, "checkpoint_1", pass_counts={"Core": 2}, total_counts={"Core": 2})
    _write_coordinator(
        checkpoint,
        dispatches={
            "p_root/aaaa": [
                _sdk_assistant("m1", input_tokens=1, cache_read=9, cache_write=2),
                _sdk_result(input_tokens=3, cache_read=900, cache_write=7),
            ],
            "n-0001/bbbb": [_sdk_result(input_tokens=10, cache_read=900, cache_write=20)],
        },
        records=[_record("p_root")],
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.new_tokens == 3 + 7 + 10 + 20


def test_a_coordinator_s_peak_is_a_single_request_not_a_dispatch_total(tmp_path: Path) -> None:
    """The cumulative `ResultMessage` must never enter the peak.

    Before the fix it did - the exclusion tested `type == "result"` and the SDK writes
    `ResultMessage` - which reported a peak of 1,874,489 against a 200,000 context window.
    """
    checkpoint = _write_checkpoint(tmp_path, "checkpoint_1", pass_counts={"Core": 2}, total_counts={"Core": 2})
    _write_coordinator(
        checkpoint,
        dispatches={
            "n-0001/bbbb": [
                _sdk_assistant("m1", input_tokens=1, cache_read=50, cache_write=9),
                _sdk_assistant("m2", input_tokens=2, cache_read=80, cache_write=8),
                _sdk_result(input_tokens=3, cache_read=999_999, cache_write=99),
            ]
        },
        records=[_record("n-0001")],
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.peak_prompt_tokens == 2 + 80 + 8


def test_a_repeated_sdk_message_id_does_not_raise_the_coordinator_peak(tmp_path: Path) -> None:
    """The SDK repeats a message per content block, exactly as the CLI stream does."""
    checkpoint = _write_checkpoint(tmp_path, "checkpoint_1", pass_counts={"Core": 2}, total_counts={"Core": 2})
    _write_coordinator(
        checkpoint,
        dispatches={
            "n-0001/bbbb": [
                _sdk_assistant("m1", input_tokens=1, cache_read=50, cache_write=9),
                _sdk_assistant("m1", input_tokens=1, cache_read=50, cache_write=9),
                _sdk_result(input_tokens=1, cache_read=50, cache_write=9),
            ]
        },
        records=[_record("n-0001")],
    )
    reading = read_checkpoint(checkpoint, problem="p")
    assert reading is not None
    assert reading.distinct_agent_messages == 1
