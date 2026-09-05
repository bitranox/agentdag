"""Tests for the SlopCodeBench arm report.

The report is what the write-ups quote, so the two things pinned are the ones that would mislead
silently: a void checkpoint that still counted in ``S``, and a curve built in directory order
rather than the pre-registered checkpoint order.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scb_arm_report import main

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

pytestmark = pytest.mark.os_agnostic

_ORPHANED: dict[str, object] = {
    "type": "system",
    "subtype": "task_notification",
    "summary": "Orphaned by a previous Claude Code process exit and reported in an aggregate summary.",
}


def _checkpoint(
    problem_dir: Path,
    name: str,
    *,
    passed: int,
    total: int,
    new_tokens: int,
    prelude: Sequence[dict[str, object]] = (),
) -> None:
    checkpoint = problem_dir / name
    (checkpoint / "agent").mkdir(parents=True)
    (checkpoint / "evaluation.json").write_text(
        json.dumps(
            {
                "pass_counts": {"Core": passed},
                "total_counts": {"Core": total},
                "pytest_collected": total,
                "infrastructure_failure": False,
                "tests": {},
            }
        )
    )
    (checkpoint / "inference_result.json").write_text(
        json.dumps({"elapsed": 10.0, "had_error": False, "usage": {"cost": 2.0, "steps": 1, "current_tokens": {}}})
    )
    events: list[dict[str, object]] = [
        *prelude,
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {"id": f"msg_{name}", "usage": {"input_tokens": new_tokens, "cache_creation_input_tokens": 0}},
        },
        {"type": "result", "subtype": "success", "num_turns": 1},
    ]
    (checkpoint / "agent" / "stdout.jsonl").write_text("\n".join(json.dumps(e) for e in events))


def _run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    for problem in ("zeta", "alpha"):
        (run / problem).mkdir(parents=True)
        (run / problem / "problem.yaml").write_text("name: x\n")
    _checkpoint(run / "zeta", "checkpoint_1", passed=1, total=1, new_tokens=100)
    _checkpoint(run / "zeta", "checkpoint_2", passed=1, total=2, new_tokens=200)
    _checkpoint(run / "alpha", "checkpoint_1", passed=1, total=1, new_tokens=50)
    return run


def test_the_curve_follows_the_given_order_not_the_directory_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _run_dir(tmp_path)
    assert main([str(run), "--order", "zeta,alpha", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["S"] == 2
    assert report["summary"]["checkpoints"] == 3
    assert report["summary"]["new_tokens"] == 350
    assert report["curve"] == [[100, 1], [300, 1], [350, 2]]


def test_a_bound_hit_voids_the_whole_problem_and_fails_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _run_dir(tmp_path)
    _checkpoint(run / "alpha", "checkpoint_2", passed=1, total=1, new_tokens=10, prelude=[_ORPHANED])
    assert main([str(run), "--order", "zeta,alpha", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["void"] == [{"problem": "alpha", "checkpoint": "checkpoint_2", "conditions": [3]}]
    assert report["void_problems"] == ["alpha"]
    assert report["summary"]["S"] == 1
    assert report["summary"]["checkpoints"] == 2
    assert report["all"]["S"] == 3


def test_the_markdown_report_has_one_row_per_checkpoint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _run_dir(tmp_path)
    assert main([str(run), "--order", "zeta,alpha"]) == 0
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if line.startswith("| `")]
    assert [r.split("|")[1].strip() for r in rows] == ["`zeta`", "`zeta`", "`alpha`"]


def test_a_missing_run_dir_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "absent"), "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is False
    assert "absent" in captured.err
