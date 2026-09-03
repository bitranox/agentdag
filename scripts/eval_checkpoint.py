"""The checkpoint instrument for the `spec` round 2 probe: when did an arm first saturate?

Round 2 measures **new tokens and wall clock at the first crossing of the case's full score**
rather than the score at a ceiling, because the case saturates: a sonnet control reached 81/81
with a third of the token ceiling unspent, so score cannot separate a competent coordinator from
a competent single agent here. That makes the crossing detector the instrument, and
`docs/probes/2026-09-03-spec-round2.md` pre-registers two properties of it as preconditions.

**It must discriminate.** Scored against an untouched seed it must read 0/81 and against the
case's reference implementation 81/81. A constant reading is a broken instrument, not agreement,
and `tests/test_eval_checkpoint.py` holds that as a test rather than a one-off command.

**It must say when it is racing the writer.** A checkpoint can catch a half-written tree, which
reads low, so only an upward crossing counts. One low reading after a higher one is a
half-written tree; two consecutively means the scorer is reading faster than the tree settles,
and the pre-registration voids that arm rather than reporting it.

Scoring is delegated to agentswarm's own `cases` module, invoked through its interpreter, because
`agentswarm` is not installed here and a reimplemented scorer is a different instrument wearing
the same name. It costs 0.2 s per call on this case, which is why every node landing can afford
one.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

__all__ = [
    "BUILD_OUTPUT",
    "CONSECUTIVE_LOWS_TO_SUSPECT",
    "CaseScorer",
    "CheckpointRun",
    "Checkpoints",
    "Reading",
    "ScoringError",
    "snapshot",
]

# Directories that are build output of whoever last ran the tree's tests, never work an agent did.
BUILD_OUTPUT = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git", ".venv")

SCORE_TIMEOUT_S = 900.0

# How many consecutive readings below the running maximum mean the scorer is reading faster
# than the tree settles. Pre-registered as two: one low reading is a half-written tree.
CONSECUTIVE_LOWS_TO_SUSPECT = 2


class ScoringError(RuntimeError):
    """The scorer could not produce a reading, which is not the same as a reading of zero."""


@dataclass(frozen=True)
class Reading:
    """One checkpoint: what the hidden suite said, and what the arm had spent when it said it.

    The spend fields default because the scorer knows the verdict and not the cost; the run fills
    them in. Keeping one record rather than a score type plus a checkpoint type means the two
    cannot drift apart.
    """

    passed: int
    failed: int
    errors: int
    total: int
    max_score: int | None
    label: str = ""
    at_s: float = 0.0
    new_tokens: int = 0

    @property
    def saturated(self) -> bool:
        """Whether this reading crosses the case's declared score.

        `>=` rather than `==` because a hidden suite may hold more tests than the case declared
        available, and a run that passes all of them has crossed.
        """
        return self.max_score is not None and self.passed >= self.max_score

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Checkpoints:
    """The ordered readings of one arm, and the two verdicts the pre-registration asks of them."""

    max_score: int
    readings: list[Reading] = field(default_factory=list[Reading])
    failures: list[str] = field(default_factory=list[str])

    def record(self, reading: Reading) -> None:
        self.readings.append(reading)

    def record_failure(self, reason: str) -> None:
        """A checkpoint that could not be scored, kept apart from one that scored zero."""
        self.failures.append(reason)

    @property
    def crossing(self) -> Reading | None:
        """The FIRST saturating reading, or None when the arm never crossed."""
        return next((r for r in self.readings if r.saturated), None)

    @property
    def racing_suspected(self) -> bool:
        """Whether two consecutive readings fell below the highest reading seen before them."""
        best = None
        consecutive_lows = 0
        for r in self.readings:
            if best is not None and r.passed < best:
                consecutive_lows += 1
                if consecutive_lows >= CONSECUTIVE_LOWS_TO_SUSPECT:
                    return True
            else:
                consecutive_lows = 0
            best = r.passed if best is None else max(best, r.passed)
        return False

    def as_dict(self) -> dict[str, Any]:
        crossing = self.crossing
        return {
            "max_score": self.max_score,
            "checkpoints_scored": len(self.readings),
            "checkpoints_failed": len(self.failures),
            "failures": self.failures[:5],
            "best_passed": max((r.passed for r in self.readings), default=None),
            "racing_suspected": self.racing_suspected,
            "crossing": crossing.as_dict() if crossing else None,
            "readings": [r.as_dict() for r in self.readings],
        }


def snapshot(workspace: Path, dest: Path) -> Path:
    """Copy a live tree so the scorer reads a stable one and writes bytecode into neither.

    Scoring imports the workspace, so scoring it in place writes `__pycache__` into a directory an
    agent is still editing. The case excludes bytecode from its own seed for exactly that reason,
    and CPython validates a `.pyc` on the source's mtime second and size, so a same-second edit of
    the same length is invisible to the check that would otherwise save it.

    Args:
        workspace: The tree being written by the arm under measurement.
        dest: Where to put the copy; created if absent.

    Returns:
        `dest`, for use as the scorer's argument.
    """
    shutil.copytree(
        workspace,
        dest,
        ignore=shutil.ignore_patterns(*BUILD_OUTPUT),
        dirs_exist_ok=True,
        symlinks=True,
    )
    return dest


@dataclass(frozen=True)
class CaseScorer:
    """Scores a workspace by invoking agentswarm's `cases` entry point under its interpreter.

    `pythonpath` exists so a round can pin the scorer to a source tree and an interpreter that
    nothing else maintains. The obvious choice - the case repo's own checkout and venv - is
    maintained by whoever is working in it: a gate there re-syncs that venv under a running arm,
    and an arm costs an hour. A checkpoint that fails is recorded rather than fatal, so the
    damage would not be a crash but a MISSED crossing, which moves the very number being
    measured.
    """

    python: Path
    case: str
    pythonpath: Path | None = None
    timeout_s: float = SCORE_TIMEOUT_S

    def child_env(self) -> dict[str, str] | None:
        """The child's environment, or None to inherit this process's.

        Built by merging rather than replacing: a bare dict drops everything the interpreter
        needs, and on Windows the lost SystemRoot kills the child outright with empty output.
        """
        if self.pythonpath is None:
            return None
        return {**os.environ, "PYTHONPATH": str(self.pythonpath)}

    def __call__(self, workspace: Path) -> Reading:
        """Score one workspace.

        Raises:
            ScoringError: The scorer exited non-zero, printed something unreadable, or reported an
                absent measurement. None of those is a score of zero.
        """
        argv = [str(self.python), "-m", "agentswarm.cases", "score", self.case, str(workspace)]
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell, paths built here
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                check=False,
                env=self.child_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScoringError(f"scoring {workspace} did not run: {exc}") from exc
        if done.returncode != 0:
            raise ScoringError(f"scoring {workspace} exited {done.returncode}: {done.stderr.strip()[:300]}")
        return _reading_from_envelope(done.stdout, workspace)


def _reading_from_envelope(stdout: str, workspace: Path) -> Reading:
    """Parse the scorer's JSON envelope, refusing an absent measurement."""
    try:
        envelope: dict[str, Any] = json.loads(stdout)
    except ValueError as exc:
        raise ScoringError(f"scoring {workspace} printed no JSON: {stdout.strip()[:200]}") from exc
    raw_data = envelope.get("data")
    data = cast("dict[str, Any]", raw_data) if isinstance(raw_data, dict) else {}
    result = data.get("result")
    if not isinstance(result, dict):
        raise ScoringError(f"scoring {workspace} reported no result, which is not a zero")
    # json.loads is Any all the way down, so this cast is where the file declares the wire shape.
    counts = cast("dict[str, Any]", result)
    return Reading(
        passed=int(counts["passed"]),
        failed=int(counts["failed"]),
        errors=int(counts["errors"]),
        total=int(counts["total"]),
        max_score=None if counts["max_score"] is None else int(counts["max_score"]),
    )


class CheckpointRun:
    """Drives the instrument for one arm: snapshot, score, record, and answer whether it crossed.

    Both arms use this, so the crossing is detected by the same code for the coordinator and for
    the single agent. An arm-specific detector would make the two numbers incomparable in a way
    no gate would show.
    """

    def __init__(self, *, scorer: CaseScorer, workspace: Path, max_score: int, started: float | None = None) -> None:
        self.scorer = scorer
        self.workspace = workspace
        self.log = Checkpoints(max_score=max_score)
        self.started = time.monotonic() if started is None else started

    @property
    def crossed(self) -> bool:
        """Whether the arm has already saturated, which is where it stops."""
        return self.log.crossing is not None

    def check(self, *, label: str, new_tokens: int) -> Reading | None:
        """Take one checkpoint. Returns the reading, or None when it could not be scored.

        A checkpoint that fails does not end the arm - one unreadable snapshot is not a reason to
        abandon an hour of work - but it is counted, because an arm whose every checkpoint failed
        reports 'never crossed' from an instrument that never spoke.
        """
        if not self.workspace.is_dir():
            self.log.record_failure(f"{label}: workspace {self.workspace} is not there")
            return None
        with tempfile.TemporaryDirectory(prefix="checkpoint-") as tmp:
            try:
                reading = self.scorer(snapshot(self.workspace, Path(tmp) / "ws"))
            except (ScoringError, OSError) as exc:
                self.log.record_failure(f"{label}: {exc}")
                return None
        stamped = dataclasses.replace(
            reading, label=label, at_s=round(time.monotonic() - self.started, 1), new_tokens=new_tokens
        )
        self.log.record(stamped)
        return stamped

    def as_dict(self) -> dict[str, Any]:
        return self.log.as_dict()
