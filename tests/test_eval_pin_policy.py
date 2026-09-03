"""Arm P of the `spec` round 2 probe: what "pinned to one model row" means, mechanically.

The round exists because round 1 could not tell decomposition from model tier: its held-fixed
table claimed a single worker tier while the planner resolved a role per node and ran three
different models. Arm P removes that axis by pinning every role to one row, so P against the
single-agent control differs only in whether the work was decomposed.

Pinning is generated from the shipped table rather than hand-edited, and asserted here through
agentdag's OWN loader and resolver - a YAML-text assertion would pass on a table the kernel
refuses to load.
"""

from __future__ import annotations

from pathlib import Path

import eval_pin_policy as pin
import pytest

from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.domain.models import TierRole
from agentdag.domain.policy import resolve_row

SHIPPED = Path(__file__).resolve().parents[1] / "src" / "agentdag" / "policy" / "tier-policy.yaml"


def test_the_shipped_table_resolves_roles_to_four_different_rows() -> None:
    """The control for the test below: without pinning, the roles really do differ.

    Asserting only that the pinned table is uniform would pass just as well against a shipped
    table that was already uniform, which would make the whole arm vacuous.
    """
    table = load_policy(SHIPPED).table
    resolved = {role: resolve_row(table, tier_role=role, model=None).alias for role in TierRole}
    assert len(set(resolved.values())) > 1, resolved


def test_every_role_resolves_to_the_pinned_row(tmp_path: Path) -> None:
    dest = pin.pin_policy(SHIPPED, tmp_path / "armP-policy.yaml", alias="sonnet")

    table = load_policy(dest).table
    for role in TierRole:
        assert resolve_row(table, tier_role=role, model=None).alias == "sonnet"


def test_the_pinned_row_is_the_only_available_one(tmp_path: Path) -> None:
    """An unavailable row cannot be offered to the planner, so it cannot be resolved by name."""
    dest = pin.pin_policy(SHIPPED, tmp_path / "armP-policy.yaml", alias="sonnet")

    table = load_policy(dest).table
    available = [row.alias for row in table.models if row.available]
    assert available == ["sonnet"]


def test_the_pinned_table_keeps_every_row_rather_than_deleting_them(tmp_path: Path) -> None:
    """The shipped table's own instruction: retiring a model is flipping `available`, never
    deleting the row. A table missing rows would also change what escalation can see."""
    dest = pin.pin_policy(SHIPPED, tmp_path / "armP-policy.yaml", alias="sonnet")

    pinned = [row.alias for row in load_policy(dest).table.models]
    assert pinned == [row.alias for row in load_policy(SHIPPED).table.models]


def test_pinning_to_a_row_that_is_not_there_is_refused(tmp_path: Path) -> None:
    """A typo must not yield a table with nothing available, which resolves nothing at all."""
    with pytest.raises(pin.PinningError, match="no row"):
        pin.pin_policy(SHIPPED, tmp_path / "p.yaml", alias="haiiku")


def test_pinning_to_a_row_whose_executor_is_unwired_is_refused(tmp_path: Path) -> None:
    """`codex` names an executor nothing wires, and the kernel refuses such a table at wiring.

    Refusing here names the cause; refusing at wiring names a policy file and costs a launch.
    """
    with pytest.raises(pin.PinningError, match="codex"):
        pin.pin_policy(SHIPPED, tmp_path / "p.yaml", alias="codex")
