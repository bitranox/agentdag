"""RED/GREEN tests for the 2.4 rule that needs a real filesystem: ``brief_ref`` containment.

The pure rules are tested in ``test_kernel_validate.py`` over values alone. These drive the
composed entry point with the REAL resolver adapter over a real ``tmp_path``, because the
rule exists for symlinks and a fake resolver would be the thing under test rather than the
containment.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import pytest

from agentdag.adapters.kernel.path_resolver_os import OsPathResolver
from agentdag.application.kernel.dispatchable import validate_dispatchable
from agentdag.domain.models import Budget, Isolation, Kind, NodeSpec, TierRole
from agentdag.domain.policy import RunLimits

if TYPE_CHECKING:
    from pathlib import Path


def limits(**over: Any) -> RunLimits:
    """Build run limits whose planner allowlist matches the shipped table plus decision 8."""
    base: dict[str, Any] = {
        "tokens_per_row": {"sonnet": 8_000_000},
        "deadline_ceiling_s": 5400.0,
        "per_kind_ceiling": {"work": TierRole.DEEP},
        "planner_kinds": [Kind.WORK, Kind.GATE, Kind.STAGE, Kind.APPROVE],
        "top_role_budget_floor": 0.05,
    }
    base.update(over)
    return RunLimits(**base)


def spec(**over: Any) -> NodeSpec:
    """Build a minimal spec that passes every rule, so one override isolates one rule."""
    base: dict[str, Any] = {
        "node_id": "w_one",
        "kind": Kind.WORK,
        "executor": "claude",
        "isolation": Isolation.NONE,
        "deadline_s": 60.0,
        "budget": Budget(),
    }
    base.update(over)
    return NodeSpec(**base)


def verdict_for(brief_ref: str, root: Path, **over: Any):
    """Validate one spec against ``root`` with the real resolver."""
    return validate_dispatchable(
        spec(brief_ref=brief_ref, **over), limits=limits(), brief_root=root, resolver=OsPathResolver()
    )


@pytest.mark.os_agnostic
def test_a_brief_ref_climbing_out_of_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()

    reasons = verdict_for("../secrets/brief.md", root).reasons

    assert any("brief_ref" in reason for reason in reasons)


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="symlink_to needs elevated privileges on Windows")
def test_a_brief_ref_leaving_through_a_symlink_is_refused(tmp_path: Path) -> None:
    """The rule realpaths for exactly this: the path is lexically inside and the file is not."""
    root = tmp_path / "run"
    (root / "briefs").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "brief.md").write_text("stolen", encoding="utf-8")
    (root / "briefs" / "elsewhere").symlink_to(outside)

    reasons = verdict_for("briefs/elsewhere/brief.md", root).reasons

    assert (root / "briefs" / "elsewhere" / "brief.md").is_relative_to(root)  # lexically contained
    assert any("brief_ref" in reason for reason in reasons)


@pytest.mark.os_agnostic
def test_an_absolute_brief_ref_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "run"
    (root / "briefs").mkdir(parents=True)
    inside = root / "briefs" / "brief.md"
    inside.write_text("brief", encoding="utf-8")

    reasons = verdict_for(str(inside), root).reasons

    assert any("absolute" in reason for reason in reasons)


@pytest.mark.os_agnostic
def test_a_brief_ref_inside_the_root_passes(tmp_path: Path) -> None:
    root = tmp_path / "run"
    (root / "briefs").mkdir(parents=True)
    (root / "briefs" / "brief.md").write_text("brief", encoding="utf-8")

    verdict = verdict_for("briefs/brief.md", root)

    assert verdict.ok
    assert verdict.complete


@pytest.mark.os_agnostic
def test_a_spec_carrying_no_brief_ref_is_not_refused_for_it(tmp_path: Path) -> None:
    """Empty is vacuously satisfied, not skipped: there was nothing to check."""
    root = tmp_path / "run"
    root.mkdir()

    verdict = verdict_for("", root)

    assert verdict.ok
    assert verdict.complete


@pytest.mark.os_agnostic
def test_the_pure_rules_and_the_path_rule_report_in_one_verdict(tmp_path: Path) -> None:
    """One entry point, or a caller checks one list and misses the other."""
    root = tmp_path / "run"
    root.mkdir()

    reasons = verdict_for("../escape.md", root, kind=Kind.APPLY, executor="code").reasons

    assert any("planner-emittable" in reason for reason in reasons)
    assert any("brief_ref" in reason for reason in reasons)
