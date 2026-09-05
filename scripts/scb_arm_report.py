"""Render one SlopCodeBench arm's readings the way the probe notes quote them.

`docs/probes/2026-09-05-slopcodebench-corrected-pair.md` pre-registers per-checkpoint readings, a
per-arm summary, four void conditions and a score curve. `slopcodebench_readings.py` computes the
readings; this script folds them into that shape so no number in a write-up is typed by hand.

An arm is one or more run directories (the launcher gives each problem its own). Problems are
laid out in the pre-registered order given by ``--order``; the curve is the running count of
strict-perfect checkpoints against cumulative new tokens in that order.

A checkpoint that meets a void condition the run directory can show (1: its tests did not
execute, 3: a CLI process hit its turn bound with work in flight) is listed under ``void``, and
the whole problem it belongs to is excluded from the tallied ``summary`` and ``curve``, as the
pre-registration prescribes. The untallied figures over every checkpoint are reported under
``all`` so the exclusion is visible rather than silent. Condition 2 (an auth-failure termination)
surfaces as the harness's ``had_error``; condition 4 (token expiry) is the launcher's record and
is not read here.

Exit codes: 0 when no void condition fired, 1 when one did, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from slopcodebench_readings import CheckpointReading, ProblemReading, collect_run

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ["ArmReport", "ArmSummary", "VoidMark", "build_report", "main", "render_json", "render_markdown"]

_TABLE_HEADER = (
    "| problem | ck | strict | core | iso | peak prompt | new tokens | cost USD | seconds | steps "
    "| results | failed own | failed inherited | regression tests | repaired | void |"
)
_TABLE_RULE = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
# The strain thresholds the calibration write-up reports occupancy against.
_STRAIN_HIGH = 150_000
_STRAIN_LIMIT = 200_000


@dataclass(frozen=True)
class VoidMark:
    """One checkpoint that met a void condition, with the conditions it met."""

    problem: str
    checkpoint: str
    conditions: list[int]


@dataclass(frozen=True)
class ArmSummary:
    """The pre-registered per-arm figures over a set of checkpoints."""

    checkpoints: int
    S: int
    C: float | None
    mean_strict: float | None
    repaired_total: int
    repaired_defined: int
    new_tokens: int
    cost_usd: float
    seconds: float
    peak_over_150k: int
    peak_over_200k: int


@dataclass(frozen=True)
class ArmReport:
    """Everything the write-up quotes, tallied and untallied."""

    problems: list[ProblemReading]
    void: list[VoidMark]
    void_problems: list[str]
    summary: ArmSummary
    all: ArmSummary
    curve: list[tuple[int, int]]


def _void_conditions(reading: CheckpointReading) -> list[int]:
    conditions: list[int] = []
    if reading.infrastructure_failure:
        conditions.append(1)
    if reading.had_error:
        conditions.append(2)
    if reading.bound_hit:
        conditions.append(3)
    return conditions


def _summarise(checkpoints: Sequence[CheckpointReading]) -> ArmSummary:
    cored = [c.core_pass_rate for c in checkpoints if c.core_total > 0]
    return ArmSummary(
        checkpoints=len(checkpoints),
        S=sum(1 for c in checkpoints if c.strict_pass_rate == 1.0),
        C=sum(cored) / len(cored) if cored else None,
        mean_strict=sum(c.strict_pass_rate for c in checkpoints) / len(checkpoints) if checkpoints else None,
        repaired_total=sum(c.repaired for c in checkpoints if c.repaired is not None),
        repaired_defined=sum(1 for c in checkpoints if c.repaired is not None),
        new_tokens=sum(c.new_tokens for c in checkpoints),
        cost_usd=sum(c.cost for c in checkpoints),
        seconds=sum(c.elapsed for c in checkpoints),
        peak_over_150k=sum(1 for c in checkpoints if c.peak_prompt_tokens > _STRAIN_HIGH),
        peak_over_200k=sum(1 for c in checkpoints if c.peak_prompt_tokens > _STRAIN_LIMIT),
    )


def _curve(checkpoints: Iterable[CheckpointReading]) -> list[tuple[int, int]]:
    spent = solved = 0
    points: list[tuple[int, int]] = []
    for reading in checkpoints:
        spent += reading.new_tokens
        solved += reading.strict_pass_rate == 1.0
        points.append((spent, solved))
    return points


def _ordered(problems: Iterable[ProblemReading], order: Sequence[str]) -> list[ProblemReading]:
    rank = {name: index for index, name in enumerate(order)}
    return sorted(problems, key=lambda p: (rank.get(p.problem, len(rank)), p.problem))


def build_report(run_dirs: Sequence[Path], *, order: Sequence[str]) -> ArmReport:
    """Collect every run directory of an arm and fold it into the report."""
    problems = _ordered((p for run in run_dirs for p in collect_run(run)), order)
    every = [c for p in problems for c in p.checkpoints]
    void = [VoidMark(c.problem, c.checkpoint, _void_conditions(c)) for c in every if _void_conditions(c)]
    void_problems = sorted({mark.problem for mark in void})
    tallied = [c for c in every if c.problem not in void_problems]
    return ArmReport(
        problems=problems,
        void=void,
        void_problems=void_problems,
        summary=_summarise(tallied),
        all=_summarise(every),
        curve=_curve(tallied),
    )


def _row(reading: CheckpointReading, *, void: bool) -> str:
    repaired = "-" if reading.repaired is None else str(reading.repaired)
    number = reading.checkpoint.rsplit("_", 1)[-1]
    return (
        f"| `{reading.problem}` | {number} | {reading.strict_pass_rate:.3f} | {reading.core_pass_rate:.3f} "
        f"| {reading.isolated_pass_rate:.3f} | {reading.peak_prompt_tokens:,} | {reading.new_tokens:,} "
        f"| {reading.cost:.2f} | {reading.elapsed:.0f} | {reading.steps} | {reading.result_events} "
        f"| {reading.failed_own} | {reading.failed_inherited} | {reading.regression_total} | {repaired} "
        f"| {'VOID' if void else ''} |"
    )


def _summary_lines(title: str, summary: ArmSummary) -> list[str]:
    c = "-" if summary.C is None else f"{summary.C:.3f}"
    strict = "-" if summary.mean_strict is None else f"{summary.mean_strict:.3f}"
    return [
        f"**{title}** ({summary.checkpoints} checkpoints): S = {summary.S}, C = {c}, mean strict = {strict}, "
        f"repaired {summary.repaired_total} of {summary.repaired_defined} defined, "
        f"new tokens {summary.new_tokens:,}, cost USD {summary.cost_usd:.2f}, seconds {summary.seconds:.0f}, "
        f"peak over 150k on {summary.peak_over_150k}, over 200k on {summary.peak_over_200k}.",
    ]


def render_markdown(report: ArmReport) -> str:
    """The write-up's table, void census, summaries and curve."""
    lines = [_TABLE_HEADER, _TABLE_RULE]
    lines.extend(_row(c, void=c.problem in report.void_problems) for p in report.problems for c in p.checkpoints)
    lines.append("")
    if report.void:
        marks = "; ".join(
            f"`{m.problem}` {m.checkpoint} (condition {', '.join(map(str, m.conditions))})" for m in report.void
        )
        excluded = ", ".join(f"`{p}`" for p in report.void_problems)
        lines.append(f"Void: {marks}")
        lines.append(f"Problems excluded from the tally: {excluded}.")
    else:
        lines.append("Void: none.")
    lines.append("")
    lines.extend(_summary_lines("Tallied", report.summary))
    if report.void:
        lines.extend(_summary_lines("All checkpoints, untallied", report.all))
    lines.append("")
    curve = ", ".join(f"({spent:,}, {solved})" for spent, solved in report.curve)
    lines.append(f"Curve (cumulative new tokens, running S): {curve}")
    return "\n".join(lines) + "\n"


def render_json(report: ArmReport) -> str:
    """The same report as one JSON object."""
    payload = {
        "ok": True,
        "problems": [
            {"problem": p.problem, "checkpoints": [asdict(c) for c in p.checkpoints]} for p in report.problems
        ],
        "void": [asdict(m) for m in report.void],
        "void_problems": report.void_problems,
        "summary": asdict(report.summary),
        "all": asdict(report.all),
        "curve": [list(point) for point in report.curve],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: run directories in, the report out."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dirs", nargs="+", type=Path, help="the arm's run directories, one or more")
    parser.add_argument(
        "--order",
        default="circuit_eval,database_migration,dynamic_config_service_api",
        help="comma-separated problem order for the table and the curve",
    )
    parser.add_argument("--json", action="store_true", help="emit one JSON object instead of Markdown")
    args = parser.parse_args(argv)
    missing = [str(run) for run in args.run_dirs if not run.is_dir()]
    if missing:
        print(f"not a directory: {', '.join(missing)}", file=sys.stderr)  # noqa: T201 - the refusal must be seen
        if args.json:
            print(json.dumps({"ok": False, "error": "not a directory", "paths": missing}))  # noqa: T201 - the envelope
        return 2
    report = build_report(args.run_dirs, order=[name for name in args.order.split(",") if name])
    sys.stdout.write(render_json(report) if args.json else render_markdown(report))
    return 1 if report.void else 0


if __name__ == "__main__":
    sys.exit(main())
