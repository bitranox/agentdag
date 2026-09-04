"""Tests for the arm launcher's token-window guard and its argument shape.

The guard is the reason the launcher exists: a problem the token cannot cover must not start.
"""

from __future__ import annotations

import argparse

import pytest
from scb_run_arm import parse_problem, token_covers

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
