"""Tests for the arm launcher's token-window guard and its argument shape.

The guard is the reason the launcher exists: a problem the token cannot cover must not start.
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

import pytest
from scb_run_arm import ProblemPlan, Waiting, await_token, parse_problem, token_covers

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.os_agnostic


def test_token_that_outlives_the_expected_duration_with_margin_covers() -> None:
    now = 1_000_000.0
    expires_ms = int((now + 1200 * 1.2 + 1) * 1000)
    assert token_covers(expires_at_ms=expires_ms, now_s=now, expected_s=1200)


def test_token_that_ends_inside_the_margin_does_not_cover() -> None:
    """Outliving the bare duration is not enough: the margin is what protects a slow problem."""
    now = 1_000_000.0
    expires_ms = int((now + 1200 * 1.1) * 1000)
    assert not token_covers(expires_at_ms=expires_ms, now_s=now, expected_s=1200)


def test_an_already_expired_token_does_not_cover() -> None:
    now = 1_000_000.0
    assert not token_covers(expires_at_ms=int((now - 1) * 1000), now_s=now, expected_s=1)


def test_parse_problem_reads_name_and_seconds() -> None:
    plan = parse_problem("circuit_eval:7980")
    assert (plan.name, plan.expected_seconds) == ("circuit_eval", 7980)


@pytest.mark.parametrize("text", ["circuit_eval", "circuit_eval:", ":7980", "circuit_eval:soon"])
def test_parse_problem_rejects_a_malformed_argument(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_problem(text)


# --- Waiting for the token, rather than refusing and making the operator relaunch -------------
#
# The guard refuses a problem the token cannot cover, which is right: a problem that outlives its
# token dies mid-run and is VOID. But refusing is only half an answer when the token is ABOUT to
# roll, and three bespoke wrapper scripts were written to sit on the credential file and relaunch.
# `await_token` is that wait, inside the launcher, so the wrappers can go.
#
# The case that matters is the ROLL: the credential is re-read on every poll, because what the
# wait exists to catch is the file changing under it. Measured 2026-09-11: a real token sat at
# 0.14 h, rolled to 7.98 h, and the wrapper caught it within five minutes.


def _credential(tmp_path: Path, *, expires_at_ms: int) -> Path:
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "t", "expiresAt": expires_at_ms}}))
    return path


def test_a_token_that_already_covers_is_returned_without_waiting(tmp_path: Path) -> None:
    now = 1_000_000.0
    path = _credential(tmp_path, expires_at_ms=int((now + 99_999) * 1000))
    slept: list[float] = []

    found = await_token(
        ProblemPlan("p", 1200),
        credentials=path,
        deadline_s=now + 600,
        waiting=Waiting(now=lambda: now, sleep=slept.append),
    )

    assert found is not None
    assert slept == [], "it waited on a token that already covered the problem"


def test_a_token_that_rolls_during_the_wait_is_picked_up(tmp_path: Path) -> None:
    """The whole point: the credential is re-read every poll, so a roll ends the wait."""
    clock = iter([1_000_000.0, 1_000_100.0, 1_000_200.0, 1_000_300.0])
    ticks = [next(clock)]
    path = _credential(tmp_path, expires_at_ms=int((ticks[0] + 60) * 1000))

    def now() -> float:
        return ticks[-1]

    def sleep(_seconds: float) -> None:
        ticks.append(next(clock))
        if len(ticks) == 3:  # the token rolls while we are waiting
            rolled = {"accessToken": "rolled", "expiresAt": int((ticks[-1] + 99_999) * 1000)}
            path.write_text(json.dumps({"claudeAiOauth": rolled}))

    found = await_token(
        ProblemPlan("p", 1200),
        credentials=path,
        deadline_s=1_000_000.0 + 6000,
        waiting=Waiting(now=now, sleep=sleep),
    )

    assert found is not None
    assert found[0] == "rolled", "it returned the stale token rather than re-reading the rolled one"


def test_a_wait_that_runs_out_gives_up_rather_than_starting(tmp_path: Path) -> None:
    """Giving up must NOT start the problem: a mid-run expiry is what the guard exists to prevent."""
    ticks = [1_000_000.0]
    path = _credential(tmp_path, expires_at_ms=int((ticks[0] + 60) * 1000))

    def sleep(_seconds: float) -> None:
        ticks.append(ticks[-1] + 1000.0)

    found = await_token(
        ProblemPlan("p", 1200),
        credentials=path,
        deadline_s=1_000_000.0 + 2000,
        waiting=Waiting(now=lambda: ticks[-1], sleep=sleep),
    )

    assert found is None


def test_the_wait_never_sleeps_past_its_own_deadline(tmp_path: Path) -> None:
    """A 1 s wait must not sleep the 300 s poll interval.

    Found by running the real argv rather than by a unit test: `--wait-for-token 1` hung for the
    full poll interval before re-checking a deadline one second away. The arms above inject
    `sleep`, so the overshoot is invisible to them - they see the CALL, not its duration.
    """
    ticks = [1_000_000.0]
    path = _credential(tmp_path, expires_at_ms=int((ticks[0] + 60) * 1000))
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        ticks.append(ticks[-1] + seconds)

    await_token(
        ProblemPlan("p", 1200),
        credentials=path,
        deadline_s=1_000_000.0 + 1.0,
        waiting=Waiting(now=lambda: ticks[-1], sleep=sleep, poll_s=300.0),
    )

    assert slept, "it never waited at all, so this arm proves nothing about the sleep it takes"
    assert max(slept) <= 1.0, f"slept past the deadline: {slept}"
