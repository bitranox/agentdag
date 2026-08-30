"""RED/GREEN test for the four run-level bounds of ``RunLimits``, and their shipped values.

Three of the four are enforced, each somewhere different, and each covered where it binds:
``max_nodes_per_plan`` by ``validate_plan``'s size rule (``test_kernel_plan_validate.py``),
``max_nodes_per_run`` and ``max_plan_depth`` by the execute loop (``test_kernel_execute.py``).
``max_replans`` is enforced by the execute loop's re-plan allowance (Task 35), covered in
``test_kernel_replan.py``; what this file proves for it is that it round-trips through the
domain model and the shipped table.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.domain.policy import RunLimits


def test_run_limits_parses_every_run_level_bound() -> None:
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
            "max_plan_depth": 4,
        }
    )
    assert limits.max_replans == 3
    assert limits.max_nodes_per_run == 200
    assert limits.max_nodes_per_plan == 40
    assert limits.max_plan_depth == 4


def test_shipped_policy_carries_the_same_bounds() -> None:
    """The shipped table's run_limits, at the ceilings Checkpoint A and B set.

    Both node ceilings are 1000 (Checkpoint A, 2026-08-29): the earlier 40 was calibrated on
    what E1's planners produced, so it refused a legitimately WIDE plan for its width alone.
    ``max_plan_depth`` is 5 (Checkpoint B, 2026-08-29) and is NOT calibrated on anything - no
    run has nested a plan, so there is no distribution to read; it is a runaway stop, and the
    yaml says so where an operator reading the table will see it.

    This pins the SHIPPED values, so it is meant to go red when the table moves - update it
    with the table, deliberately.
    """
    path = Path(str(files("agentdag.policy") / "tier-policy.yaml"))
    limits = load_policy(path).table.run_limits
    assert limits.max_replans == 3
    assert limits.max_nodes_per_run == 1000
    assert limits.max_nodes_per_plan == 1000
    assert limits.max_plan_depth == 5
