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
                    "steps": 3,
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
