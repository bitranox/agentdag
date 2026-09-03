"""The checkpoint instrument: what counts as a crossing, and when it is not to be believed.

Round 2 of the `spec` probe measures the cost at the FIRST crossing of the case's full score,
so the instrument that detects a crossing decides the number. Its pre-registration
(`docs/probes/2026-09-03-spec-round2.md`) makes two properties preconditions rather than
niceties, and both are held here: it must discriminate a solved workspace from an untouched one,
and it must say when it is racing the process writing the tree instead of reporting a result.
"""

from __future__ import annotations

import os
from pathlib import Path

import eval_checkpoint as ec
import pytest

# The case and its scorer live in the agentswarm repo, a SIBLING of this one. Resolved relatively
# rather than written out: an absolute path here names one machine's mount, which this repo's own
# publishability guard refuses, and it would be wrong in any clone. `AGENTSWARM_ROOT` overrides it
# for a checkout kept somewhere else.
AGENTSWARM = Path(os.environ.get("AGENTSWARM_ROOT") or Path(__file__).resolve().parents[2] / "agentswarm")


def reading(passed: int, *, max_score: int = 81, at_s: float = 0.0, new_tokens: int = 0) -> ec.Reading:
    return ec.Reading(
        label="n",
        at_s=at_s,
        new_tokens=new_tokens,
        passed=passed,
        failed=max_score - passed,
        errors=0,
        total=max_score,
        max_score=max_score,
    )


def log_of(*scores: int) -> ec.Checkpoints:
    log = ec.Checkpoints(max_score=81)
    for i, s in enumerate(scores):
        log.record(reading(s, at_s=float(i), new_tokens=1000 * i))
    return log


# --- what counts as a crossing --------------------------------------------------------------


def test_the_first_saturating_reading_is_the_crossing() -> None:
    log = log_of(0, 40, 81)
    crossing = log.crossing
    assert crossing is not None
    assert (crossing.passed, crossing.at_s, crossing.new_tokens) == (81, 2.0, 2000)


def test_a_later_saturating_reading_does_not_move_the_crossing() -> None:
    """The measured quantity is the cost at the FIRST crossing, not at the last reading."""
    log = log_of(0, 81, 81)
    crossing = log.crossing
    assert crossing is not None
    assert crossing.at_s == 1.0


def test_an_arm_that_never_saturates_has_no_crossing() -> None:
    """Reported as an absent measurement, never as a large cost."""
    assert log_of(0, 40, 80).crossing is None


def test_a_suite_that_grows_past_the_declared_score_still_crosses() -> None:
    """`max_score` is what the case declared was available, and a suite can hold more."""
    log = ec.Checkpoints(max_score=81)
    log.record(reading(82, max_score=81))
    assert log.crossing is not None


# --- when the instrument is not to be believed -----------------------------------------------


def test_two_consecutive_readings_below_the_running_max_suspect_a_racing_scorer() -> None:
    """Pre-registered: two consecutive lows mean the scorer is reading half-written trees."""
    assert log_of(0, 60, 10, 12).racing_suspected is True


def test_a_single_low_reading_after_a_high_one_is_not_suspected() -> None:
    """Also pre-registered: one low reading is a half-written tree, not a broken instrument."""
    assert log_of(0, 60, 10, 70).racing_suspected is False


def test_a_monotonic_climb_is_never_suspected() -> None:
    assert log_of(0, 10, 40, 81).racing_suspected is False


def test_the_first_reading_cannot_be_low_because_nothing_precedes_it() -> None:
    assert log_of(0, 0).racing_suspected is False


# --- the snapshot ----------------------------------------------------------------------------


def test_a_snapshot_carries_the_work_and_leaves_build_output_behind(tmp_path: Path) -> None:
    """Scoring imports from the tree, so scoring in place writes bytecode into a live workspace.

    The case excludes `__pycache__` from its own seed for that reason, and a checkpoint that put
    it back on every poll would undo it. CPython validates a `.pyc` on the source's mtime second
    and size, so a same-second edit of the same length reads as unchanged.
    """
    live = tmp_path / "ws"
    (live / "pkg" / "__pycache__").mkdir(parents=True)
    (live / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (live / "pkg" / "__pycache__" / "mod.pyc").write_bytes(b"stale")
    (live / ".pytest_cache").mkdir()

    dest = ec.snapshot(live, tmp_path / "snap")

    assert (dest / "pkg" / "mod.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (dest / "pkg" / "__pycache__").exists()
    assert not (dest / ".pytest_cache").exists()


# --- the discrimination control, run against the real case -----------------------------------


@pytest.mark.local_only
@pytest.mark.os_agnostic
def test_the_scorer_discriminates_an_untouched_seed_from_the_reference(tmp_path: Path) -> None:
    """Requirement 1 of the round 2 pre-registration, as a test rather than a one-off command.

    A scorer that cannot tell these two apart cannot detect a crossing, and a constant reading is
    a broken instrument rather than agreement. Both ends are asserted in one test because either
    alone is satisfied by a scorer stuck on that answer.
    """
    python = AGENTSWARM / ".venv" / "bin" / "python"
    if not python.is_file():
        pytest.skip(f"the agentswarm interpreter this scorer invokes is not at {python}")
    scorer = ec.CaseScorer(python=python, case="spec")

    seed = ec.snapshot(AGENTSWARM / "cases" / "spec" / "seed", tmp_path / "seed")
    floor = scorer(seed)
    top = scorer(AGENTSWARM / "cases" / "spec" / "hidden" / "reference")

    assert (floor.passed, floor.max_score) == (0, 81)
    assert (top.passed, top.max_score) == (81, 81)
    # An untouched seed FAILS rather than ERRORS: the stubs import and raise. All-errored would
    # mean the tree would not import, which is the opposite finding and renders as the same zero.
    assert (floor.failed, floor.errors) == (81, 0)


@pytest.mark.local_only
@pytest.mark.os_agnostic
def test_the_scorer_refuses_a_workspace_that_is_not_there(tmp_path: Path) -> None:
    """A snapshot that failed to appear must raise, not score as work not done."""
    python = AGENTSWARM / ".venv" / "bin" / "python"
    if not python.is_file():
        pytest.skip(f"the agentswarm interpreter this scorer invokes is not at {python}")
    scorer = ec.CaseScorer(python=python, case="spec")

    with pytest.raises(ec.ScoringError, match="no such workspace"):
        scorer(tmp_path / "never-created")


def test_the_scorer_inherits_the_environment_when_no_pythonpath_is_pinned() -> None:
    """None means inherit, so the default path stays exactly what it was."""
    assert ec.CaseScorer(python=Path("/x"), case="spec").child_env() is None


def test_a_pinned_pythonpath_is_merged_into_the_environment_not_swapped_for_it() -> None:
    """A bare dict drops everything else the interpreter needs; on Windows that kills the child
    with empty output, which a caller reads as a program that has no options rather than a dead
    one."""
    pinned = Path("/pinned/src")
    env = ec.CaseScorer(python=Path("/x"), case="spec", pythonpath=pinned).child_env()

    assert env is not None
    # Compared through the same Path round trip, not against a literal: str(Path("/pinned/src"))
    # is a backslash path on Windows, so the literal form asserts the separator rather than the
    # value and reddens all three Windows cells while passing everywhere else.
    assert Path(env["PYTHONPATH"]) == pinned
    assert env.get("PATH") == os.environ.get("PATH")
