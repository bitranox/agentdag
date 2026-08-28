"""RED/GREEN test for the three run-level bounds Task 30 adds to ``RunLimits``.

``max_replans`` and ``max_nodes_per_run`` are parsed here and nowhere ENFORCED yet - they
bound a whole run across possibly many plans/replans, which is later work; this task only
proves they round-trip through both the domain model and the shipped policy table.
``max_nodes_per_plan`` is the one this task's own :func:`~agentdag.application.kernel.
plan_validate.validate_plan` enforces, covered separately in ``test_kernel_plan_validate.py``.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.domain.policy import RunLimits


def test_run_limits_parses_the_three_new_bounds() -> None:
    limits = RunLimits.model_validate(
        {
            "tokens_per_row": {},
            "deadline_ceiling_s": 1.0,
            "per_kind_ceiling": {},
            "planner_kinds": [],
            "top_role_budget_floor": 0.0,
            "max_replans": 3,
            "max_nodes_per_run": 200,
            "max_nodes_per_plan": 40,
        }
    )
    assert limits.max_replans == 3
    assert limits.max_nodes_per_run == 200
    assert limits.max_nodes_per_plan == 40


def test_shipped_policy_carries_the_same_three_bounds() -> None:
    """The shipped table's run_limits (E1's largest graph is 38 nodes; 40 leaves headroom)."""
    path = Path(str(files("agentdag.policy") / "tier-policy.yaml"))
    limits = load_policy(path).table.run_limits
    assert limits.max_replans == 3
    assert limits.max_nodes_per_run == 200
    assert limits.max_nodes_per_plan == 40
