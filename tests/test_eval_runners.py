"""The two runners' watching logic, exercised without spending a dispatch.

An arm costs up to an hour and real tokens, so the parts that decide when it stops are tested
against a fabricated run directory instead. The three stop conditions and the node-landing
detector are what turn a coordinator launch into a measurement; a defect in any of them is only
visible as a number that looks plausible.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import eval_checkpoint as ec
import eval_run_agentdag as rad


def write_journal(run_dir: Path, results: int) -> None:
    """A journal with `results` landed nodes, plus a trailing partial line as a live one has."""
    lines = [json.dumps({"event": "run_started"})]
    lines += [json.dumps({"event": "started", "node_id": f"n-{i}"}) for i in range(results)]
    lines += [json.dumps({"event": "result", "node_id": f"n-{i}"}) for i in range(results)]
    (run_dir / "journal.jsonl").write_text("\n".join(lines) + '\n{"event": "resu', encoding="utf-8")


def write_transcript(run_dir: Path, *, node: str, new: int, cache: int, out: int) -> None:
    d = run_dir / "nodes" / node / "abc123"
    d.mkdir(parents=True, exist_ok=True)
    usage = {"input_tokens": new, "cache_creation_input_tokens": 0, "cache_read_input_tokens": cache}
    usage["output_tokens"] = out
    partial = json.dumps({"type": "AssistantMessage", "usage": usage})
    final = json.dumps({"type": "ResultMessage", "usage": usage})
    (d / "transcript.jsonl").write_text(partial + "\n" + final + "\n", encoding="utf-8")


def fake_run(tmp_path: Path, *, results: int = 1, new: int = 1000) -> Path:
    run_dir = tmp_path / "runs" / "20260903T120000Z-aaaaaa"
    (run_dir / "wt" / "root").mkdir(parents=True)
    write_journal(run_dir, results)
    write_transcript(run_dir, node="n-0", new=new, cache=17 * new, out=50)
    return run_dir


def args_for(tmp_path: Path, *, ceiling: int = 1_000_000, deadline: float = 3600.0) -> Namespace:
    return Namespace(
        goal_file=tmp_path / "goal.txt",
        runs=tmp_path / "runs",
        log=tmp_path / "log.txt",
        ceiling=ceiling,
        deadline=deadline,
        case="spec",
        agentswarm_python=Path("/nonexistent/python"),
        max_score=81,
        policy=None,
        arm="agentdag",
    )


class ScoreOf:
    """A scorer that returns fixed readings in order, standing in for the real subprocess."""

    def __init__(self, *scores: int) -> None:
        self.scores = list(scores)
        self.calls = 0

    def __call__(self, workspace: Path) -> ec.Reading:
        i = min(self.calls, len(self.scores) - 1)
        self.calls += 1
        passed = self.scores[i]
        return ec.Reading(passed=passed, failed=81 - passed, errors=0, total=81, max_score=81)


# --- reading the run directory ---------------------------------------------------------------


def test_landed_nodes_are_counted_from_result_events(tmp_path: Path) -> None:
    run_dir = fake_run(tmp_path, results=3)
    assert rad.nodes_landed(run_dir) == 3


def test_a_partly_written_journal_line_is_skipped_not_counted(tmp_path: Path) -> None:
    """The journal is appended while this reads it, so the last line can be half a record."""
    run_dir = fake_run(tmp_path, results=2)
    assert (run_dir / "journal.jsonl").read_text(encoding="utf-8").endswith('{"event": "resu')
    assert rad.nodes_landed(run_dir) == 2


def test_a_run_directory_with_no_journal_has_landed_nothing(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert rad.nodes_landed(tmp_path / "empty") == 0


def test_new_tokens_reads_result_messages_and_ignores_the_repeats(tmp_path: Path) -> None:
    """The same usage block is emitted per content block; only the ResultMessage is charged."""
    run_dir = fake_run(tmp_path, new=1000)
    assert rad.new_tokens(run_dir) == (1000, 17000, 50)


# --- when the arm stops ------------------------------------------------------------------------


def test_a_landed_node_takes_a_checkpoint(tmp_path: Path) -> None:
    run_dir = fake_run(tmp_path, results=2)
    arm = rad.Arm(args_for(tmp_path))
    arm.scorer = ScoreOf(40)  # type: ignore[assignment]

    assert arm.stop_reason(run_dir) is None
    assert arm.checkpoints is not None
    assert [r.passed for r in arm.checkpoints.log.readings] == [40]
    assert arm.checkpoints.log.readings[0].new_tokens == 1000


def test_a_saturating_checkpoint_stops_the_arm(tmp_path: Path) -> None:
    run_dir = fake_run(tmp_path, results=1)
    arm = rad.Arm(args_for(tmp_path))
    arm.scorer = ScoreOf(81)  # type: ignore[assignment]

    assert arm.stop_reason(run_dir) == "saturated"


def test_the_token_ceiling_stops_the_arm(tmp_path: Path) -> None:
    run_dir = fake_run(tmp_path, results=1, new=5000)
    arm = rad.Arm(args_for(tmp_path, ceiling=4000))
    arm.scorer = ScoreOf(10)  # type: ignore[assignment]

    assert arm.stop_reason(run_dir) == "token ceiling reached at 5000"


def test_the_deadline_stops_the_arm(tmp_path: Path) -> None:
    run_dir = fake_run(tmp_path, results=1)
    arm = rad.Arm(args_for(tmp_path, deadline=0.0))
    arm.scorer = ScoreOf(10)  # type: ignore[assignment]

    assert arm.stop_reason(run_dir) == "wall-clock deadline reached"


def test_saturation_outranks_the_ceiling_so_a_crossing_is_never_lost(tmp_path: Path) -> None:
    """Both fire on the same poll when the last node is also the expensive one.

    Reporting the ceiling there would throw away the crossing this round exists to measure.
    """
    run_dir = fake_run(tmp_path, results=1, new=5000)
    arm = rad.Arm(args_for(tmp_path, ceiling=4000))
    arm.scorer = ScoreOf(81)  # type: ignore[assignment]

    assert arm.stop_reason(run_dir) == "saturated"


def test_nothing_is_checked_before_the_coordinator_makes_its_run_directory(tmp_path: Path) -> None:
    arm = rad.Arm(args_for(tmp_path))
    assert arm.stop_reason(None) is None
    assert arm.checkpoints is None


# --- the argv the runner builds for itself -----------------------------------------------------


def test_a_policy_reaches_the_coordinator_argv(tmp_path: Path) -> None:
    """Arm P is defined by its policy file, so an argv that drops it silently runs arm S."""
    args = args_for(tmp_path)
    args.policy = tmp_path / "armP-policy.yaml"

    argv = rad.coordinator_argv(args, "do the thing")

    assert argv[-2:] == ["--policy", str(args.policy)]
    assert "--foreground" in argv


def test_the_shipped_policy_arm_passes_no_policy_flag(tmp_path: Path) -> None:
    argv = rad.coordinator_argv(args_for(tmp_path), "do the thing")
    assert "--policy" not in argv
