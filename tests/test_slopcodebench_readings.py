"""Tests for the SlopCodeBench control-arm readings extractor.

The two cases that matter are the ones that fail SILENTLY in production: a cumulative ``result``
event counted as a request, and ``checkpoint_10`` sorting beside ``checkpoint_1``. Both produce a
plausible number rather than an error, so each is pinned here rather than checked by eye.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slopcodebench_readings import collect_problem, peak_prompt_tokens, read_checkpoint

pytestmark = pytest.mark.os_agnostic


def _write_checkpoint(
    root: Path,
    name: str,
    *,
    pass_counts: dict[str, int],
    total_counts: dict[str, int],
    usage_lines: list[dict[str, object]] | None = None,
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
        (checkpoint / "agent" / "stdout.jsonl").write_text(
            "\n".join(json.dumps(line) for line in usage_lines)
        )
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
        _write_checkpoint(
            tmp_path, name, pass_counts={"Core": 1}, total_counts={"Core": 1}, usage_lines=[]
        )
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
    _write_checkpoint(
        tmp_path, "checkpoint_1", pass_counts={"Core": 2}, total_counts={"Core": 2}, usage_lines=[]
    )
    _write_checkpoint(
        tmp_path, "checkpoint_2", pass_counts={"Core": 1}, total_counts={"Core": 2}, usage_lines=[]
    )
    assert collect_problem(tmp_path).solved == 1
