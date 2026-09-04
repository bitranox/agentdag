"""Read the pre-registered readings of the SlopCodeBench control arm out of a run directory.

`docs/probes/2026-09-04-slopcodebench-control.md` pre-registers what this arm measures, and this
script is the only thing that computes it, so a number in the write-up is never a number somebody
read off a log by eye.

Three of the quantities are recomputed here rather than taken from the harness, because the
harness reports them per checkpoint and the pre-registration asks for them per ARM:

* ``strict_pass_rate`` - all passed over all tests, INCLUDING prior checkpoints' regression tests
* ``core_pass_rate`` - the explicitly specified behaviours only
* ``isolated_pass_rate`` - this checkpoint's own tests, regression removed

The formulas are the harness's own (``src/slop_code/metrics/checkpoint/extractors.py``), applied
to each checkpoint's ``evaluation.json``.

**The strain reading needs care, and the pre-registration got its name wrong.** It was written as
``input + cache_write``, which is the protocol's NEW-TOKEN cost unit, not context occupancy: a
request's prompt is ``input + cache_read + cache_write``. Both are reported, separately and
labelled, and the write-up says which one answers which question. Occupancy is what decides
whether one agent's context was ever the binding constraint.

Two traps in the token stream, both of which silently inflate a total:

* the ``result`` event's usage is the CUMULATIVE dispatch total, so including it in a per-request
  peak reports a context far larger than any single request ever held (measured: 331,679 against
  a true peak of 55,011);
* the CLI repeats one ``message_id`` and its usage once per CONTENT BLOCK, so summing per event
  double counts. Peaks take a max and are unaffected; sums dedupe by ``message_id``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "CheckpointReading",
    "ProblemReading",
    "collect_problem",
    "collect_run",
    "peak_prompt_tokens",
    "read_checkpoint",
]


@dataclass(frozen=True)
class CheckpointReading:
    """One checkpoint's pre-registered readings."""

    problem: str
    checkpoint: str
    strict_pass_rate: float
    core_pass_rate: float
    isolated_pass_rate: float
    total_tests: int
    passed_tests: int
    core_total: int
    core_passed: int
    cost: float
    elapsed: float
    steps: int
    new_tokens: int
    """input + cache_write: the protocol's cost unit, NOT context occupancy."""
    peak_prompt_tokens: int
    """Largest single request's input + cache_read + cache_write: the occupancy reading."""
    had_error: bool
    infrastructure_failure: bool


@dataclass(frozen=True)
class ProblemReading:
    """Every checkpoint of one problem, in checkpoint order."""

    problem: str
    checkpoints: tuple[CheckpointReading, ...]

    @property
    def mean_core_pass_rate(self) -> float | None:
        """The pre-registered ``C``, over checkpoints that HAVE core tests.

        A checkpoint with no Core tests scores 0.0 from the harness's formula, which is
        indistinguishable from failing every Core test. Averaging those in would drag ``C``
        toward a band boundary for a reason that is not about the work, so they are excluded and
        ``None`` is returned when none remain.
        """
        scored = [c.core_pass_rate for c in self.checkpoints if c.core_total > 0]
        return sum(scored) / len(scored) if scored else None

    @property
    def solved(self) -> int:
        """Checkpoints whose strict pass rate is exactly 1.0 - the pre-registered ``S``."""
        return sum(1 for c in self.checkpoints if c.strict_pass_rate == 1.0)


def _rates(metrics: dict[str, Any]) -> tuple[float, float, float, int, int]:
    """Apply the harness's own pass-rate formulas to one ``evaluation.json``."""
    total_counts: dict[str, int] = metrics.get("total_counts", {})
    pass_counts: dict[str, int] = metrics.get("pass_counts", {})
    total_passed = sum(pass_counts.values())
    total_total = sum(total_counts.values())
    collected = int(metrics.get("pytest_collected", 0) or 0)
    if total_total == 0 and collected:
        total_total = collected
    regression_total = total_counts.get("Regression", 0)
    checkpoint_passed = total_passed - pass_counts.get("Regression", 0)
    checkpoint_total = total_total - regression_total
    core_total = total_counts.get("Core", 0)
    core_passed = pass_counts.get("Core", 0)
    return (
        total_passed / total_total if total_total else 0.0,
        core_passed / core_total if core_total else 0.0,
        checkpoint_passed / checkpoint_total if checkpoint_total else 0.0,
        total_total,
        total_passed,
    )


def peak_prompt_tokens(stdout_jsonl: Path) -> int:
    """Largest single request's prompt size, excluding the cumulative ``result`` event.

    A ``result`` event carries the whole dispatch's totals, so counting it as a request reports
    an occupancy no single request ever had.
    """
    if not stdout_jsonl.is_file():
        return 0
    peak = 0
    for line in stdout_jsonl.read_text(errors="replace").splitlines():
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("type") == "result":
            continue
        usage = payload.get("usage") or (payload.get("message") or {}).get("usage")
        if not isinstance(usage, dict):
            continue
        prompt = (
            int(usage.get("input_tokens", 0) or 0)
            + int(usage.get("cache_read_input_tokens", 0) or 0)
            + int(usage.get("cache_creation_input_tokens", 0) or 0)
        )
        peak = max(peak, prompt)
    return peak


def read_checkpoint(checkpoint_dir: Path, *, problem: str) -> CheckpointReading | None:
    """Read one checkpoint, or ``None`` when it has not been evaluated yet."""
    evaluation = checkpoint_dir / "evaluation.json"
    inference = checkpoint_dir / "inference_result.json"
    if not evaluation.is_file() or not inference.is_file():
        return None
    metrics: dict[str, Any] = json.loads(evaluation.read_text())
    result: dict[str, Any] = json.loads(inference.read_text())
    strict, core, isolated, total, passed = _rates(metrics)
    total_counts: dict[str, int] = metrics.get("total_counts", {})
    pass_counts: dict[str, int] = metrics.get("pass_counts", {})
    usage: dict[str, Any] = result.get("usage", {})
    current: dict[str, Any] = usage.get("current_tokens", {})
    return CheckpointReading(
        problem=problem,
        checkpoint=checkpoint_dir.name,
        strict_pass_rate=strict,
        core_pass_rate=core,
        isolated_pass_rate=isolated,
        total_tests=total,
        passed_tests=passed,
        core_total=total_counts.get("Core", 0),
        core_passed=pass_counts.get("Core", 0),
        cost=float(usage.get("cost", 0.0) or 0.0),
        elapsed=float(result.get("elapsed", 0.0) or 0.0),
        steps=int(usage.get("steps", 0) or 0),
        new_tokens=int(current.get("input", 0) or 0) + int(current.get("cache_write", 0) or 0),
        peak_prompt_tokens=peak_prompt_tokens(checkpoint_dir / "agent" / "stdout.jsonl"),
        had_error=bool(result.get("had_error", False)),
        infrastructure_failure=bool(metrics.get("infrastructure_failure", False)),
    )


def _checkpoint_order(path: Path) -> tuple[int, str]:
    """Sort ``checkpoint_10`` after ``checkpoint_9`` rather than beside ``checkpoint_1``."""
    tail = path.name.rsplit("_", 1)[-1]
    return (int(tail), path.name) if tail.isdigit() else (1 << 30, path.name)


def collect_problem(problem_dir: Path) -> ProblemReading:
    """Every evaluated checkpoint of one problem, in checkpoint order."""
    dirs = sorted(
        (d for d in problem_dir.iterdir() if d.is_dir() and d.name.startswith("checkpoint_")),
        key=_checkpoint_order,
    )
    readings = [r for d in dirs if (r := read_checkpoint(d, problem=problem_dir.name)) is not None]
    return ProblemReading(problem=problem_dir.name, checkpoints=tuple(readings))


def collect_run(run_dir: Path) -> tuple[ProblemReading, ...]:
    """Every problem in a run directory that has at least one evaluated checkpoint."""
    problems = sorted(
        d for d in run_dir.iterdir() if d.is_dir() and (d / "problem.yaml").is_file()
    )
    return tuple(p for d in problems if (p := collect_problem(d)).checkpoints)
