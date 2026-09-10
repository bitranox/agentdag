"""The shipped coordinator arm against the frozen pre-registration.

``docs/probes/2026-09-05-slopcodebench-corrected-pair.md`` fixes the corrected pair's settings
above its divider, so those lines cannot change now that a counted arm has run. Nothing else
checks the arm's own files against them: the settings live in two places (the harness agent
config and the tier policy), a default supplies a plausible value for several of them, and a
default that merely LOOKS right is exactly the failure this guards - the shipped tier table
puts every ``work`` node on sonnet, which reads as ordinary agentdag behaviour and is a model
the held-fixed table forbids.

Each assertion below names the pre-registered line it enforces. A deliberate change to the arm
has to change this file too, which is the point: the frozen table is not a comment. The same
holds for the document's addendum, pre-registered below its divider on 2026-09-10 because the
text above it cannot be edited: a decision recorded only in prose is one nothing checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.domain.models import TierRole
from agentdag.domain.policy import PolicyTable, resolve_row

pytestmark = pytest.mark.os_agnostic

ARM_ROOT = Path(__file__).resolve().parents[1] / "deploy" / "slopcodebench"
ARM_CONFIG = ARM_ROOT / "agentdag.yaml"
ARM_POLICY = ARM_ROOT / "arm-tier-policy.yaml"

PREREGISTERED_TOKENS_PER_ROW = 1_200_000
"""Decision 1: the runaway guard, per checkpoint run, in agentdag's charged unit."""

PREREGISTERED_DENY_BASH = ["git push", "gh pr", "gh release"]
"""Decision 3: publishing actions only. The default also closes ``curl -X POST`` and
``curl --data``, which the calibration control used four times on ``dynamic_config_service_api``
to exercise the HTTP API it was building."""


def arm_document() -> dict[str, object]:
    parsed = yaml.safe_load(ARM_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return cast("dict[str, object]", parsed)


def arm_cost_limits() -> dict[str, object]:
    limits = arm_document()["cost_limits"]
    assert isinstance(limits, dict)
    return cast("dict[str, object]", limits)


def arm_settings() -> dict[str, object]:
    settings = arm_document()["settings"]
    assert isinstance(settings, dict)
    return cast("dict[str, object]", settings)


def test_the_arm_denies_publishing_only_so_a_node_can_exercise_its_own_http_service() -> None:
    assert arm_settings()["kernel.deny_bash"] == PREREGISTERED_DENY_BASH


def test_the_arm_states_its_own_concurrency_rather_than_inheriting_a_default() -> None:
    """Decision 4: parallel 8. Stated, so the arm still says what it ran if the default moves."""
    assert arm_settings()["kernel.parallel"] == 8


def test_the_arm_makes_one_attempt_per_checkpoint_so_cost_and_score_describe_the_same_one() -> None:
    """Addendum, 2026-09-10: the harness base class's ``retry()`` re-dispatches ``plan-goal``
    with "Continue from where you left off." and none of the specification, so the planner
    re-plans a task it cannot see. The harness runs ``max_retries + 1`` attempts, so 0 is one
    attempt: a checkpoint's cost and its score then describe the same attempt, which the paid
    dry run's did not."""
    assert arm_cost_limits()["max_retries"] == 0


def test_the_other_cost_limits_still_mirror_the_control_the_config_says_they_mirror() -> None:
    """The arm config claims to mirror the control's ``cost_limits`` verbatim except
    ``max_retries``. The control's file lives outside this repo
    (``agent-claude-code-oauth.yaml`` beside the harness clone), so the claim is checked
    against its values rather than against the file: 0 disables the two cost ceilings on both
    arms, and 100 is what the control passes as ``--max-turns``."""
    assert arm_cost_limits() == {"cost_limit": 0, "step_limit": 100, "net_cost_limit": 0, "max_retries": 0}


def test_the_arm_names_a_tier_policy_place_the_installer_can_fill() -> None:
    """The path is a launch fact, so the committed config carries the comment, not a value."""
    assert ARM_CONFIG.read_text(encoding="utf-8").count("# policy: /abs/path/to/tier-policy.yaml") == 1
    assert arm_document().get("policy") is None


def loaded_arm_policy() -> PolicyTable:
    return load_policy(ARM_POLICY, max_turns=100, deny_bash=(), deny_tools=()).table


def test_every_role_resolves_to_opus_so_no_node_can_run_a_cheaper_tier() -> None:
    """Held fixed: "opus-5 for the control and for EVERY coordinator node; no cheaper tier anywhere"."""
    table = loaded_arm_policy()
    assert [row.alias for row in table.models if row.available] == ["opus"]
    for role in TierRole:
        assert resolve_row(table, tier_role=role, model=None).alias == "opus"


def test_every_kind_that_resolves_a_model_asks_for_high_effort() -> None:
    """Held fixed: "thinking: high ... every node receives it"."""
    table = loaded_arm_policy()
    resolving = {kind: default for kind, default in table.kind_defaults.items() if default.tier_role is not None}
    assert resolving, "a table where no kind resolves a model would satisfy this vacuously"
    assert {kind: default.effort for kind, default in resolving.items()} == dict.fromkeys(resolving, "high")


def test_the_token_guard_is_the_pre_registered_one_on_every_row() -> None:
    """Decision 1, and on the unavailable rows too, so no row can outspend the one that runs."""
    per_row = loaded_arm_policy().run_limits.tokens_per_row
    assert per_row, "an empty table declares no ceiling at all"
    assert set(per_row.values()) == {PREREGISTERED_TOKENS_PER_ROW}
