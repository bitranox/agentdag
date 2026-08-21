"""RED/GREEN tests for whole-spec validation (design 2.4).

The rules here REFUSE a planner-emitted spec with reasons. Which of them 2.3 rule 4 would
rather CLAMP is an open question for exactly one field (``tier_role``); every rule tested here
is one both design sections state the same way, so none of them turns on that answer.

Every test builds a real ``NodeSpec`` and a real ``RunLimits``; nothing is patched, because
the unit under test is a pure function over two typed values.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.domain.models import Budget, Isolation, Kind, NodeSpec, TierRole
from agentdag.domain.policy import RunLimits
from agentdag.domain.validate import validate_spec


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


def test_refuses_a_kind_outside_the_planner_allowlist() -> None:
    """``apply`` is never planner-emitted (2.4, and decision 8 keeps it out)."""
    reasons = validate_spec(spec(kind=Kind.APPLY), limits=limits())

    assert any("apply" in reason for reason in reasons), reasons


def test_admits_stage_and_approve_since_decision_8() -> None:
    """Decision 8 put ``stage`` and ``approve`` inside the allowlist; ``apply`` stays out."""
    for kind in (Kind.STAGE, Kind.APPROVE):
        reasons = validate_spec(spec(kind=kind, executor="code"), limits=limits())

        assert not any("planner-emittable" in reason for reason in reasons), (kind, reasons)


def test_refuses_a_code_kind_carrying_a_model_executor() -> None:
    """A ``gate`` runs code, so naming a model executor is a spec error (design 2.1)."""
    reasons = validate_spec(spec(kind=Kind.GATE, executor="claude"), limits=limits())

    assert any("executor" in reason for reason in reasons), reasons


def test_shipped_policy_allowlist_matches_decision_8() -> None:
    """The SHIPPED table is what binds a real run, so decision 8 is asserted against it.

    The fixture above proves the rule reads an allowlist; only this proves the allowlist
    the coordinator actually loads says what the user decided.
    """
    loaded = load_policy(Path(str(files("agentdag.policy") / "tier-policy.yaml")))
    allowed = set(loaded.table.run_limits.planner_kinds)

    assert Kind.STAGE in allowed, allowed
    assert Kind.APPROVE in allowed, allowed
    assert Kind.APPLY not in allowed, allowed


def test_refuses_a_role_on_a_kind_that_resolves_no_model_row() -> None:
    """2.1 and 2.3 rule 1 agree: code and fan-out kinds carry a null ``tier_role``.

    This is NOT the ``per_kind_ceiling`` rule, which is the contested one - it is the
    "resolves no model row at all" rule, which both design sections state the same way.
    """
    for kind, executor in ((Kind.GATE, "code"), (Kind.MAP, None)):
        reasons = validate_spec(spec(kind=kind, executor=executor, tier_role=TierRole.DEEP), limits=limits())

        assert any("tier_role" in reason for reason in reasons), (kind, reasons)


def test_admits_a_model_kind_that_leaves_the_executor_to_the_policy_row() -> None:
    """A model-executed spec may omit ``executor``: the resolved ROW supplies it.

    Graph A's shipped ``w_migrate`` does exactly this - ``tier_role`` and ``model``, no
    ``executor`` - and ``LoadedPolicy.resolve`` returns ``ResolvedRow(executor=row.executor)``.
    A rule demanding one here refuses a spec that runs today.
    """
    reasons = validate_spec(
        spec(kind=Kind.WORK, executor=None, tier_role=TierRole.STANDARD, model="sonnet"),
        limits=limits(),
    )

    assert not any("executor" in reason for reason in reasons), reasons


def test_still_refuses_a_model_kind_that_claims_the_code_executor() -> None:
    """Omitting the executor is fine; naming the CODE runner for a model kind is not."""
    reasons = validate_spec(spec(kind=Kind.WORK, executor="code"), limits=limits())

    assert any("executor" in reason for reason in reasons), reasons
