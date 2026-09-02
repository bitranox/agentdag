"""Tests for the tier policy table: YAML in, tier resolution and run limits out (design 2.3).

The shipped table (``agentdag/policy/tier-policy.yaml``) is loaded through
:func:`~agentdag.adapters.kernel.policy_yaml.load_policy` in every test here rather than a
hand-built one, so these tests also prove the shipped copy of the design's example table still
parses and resolves the way the design describes.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from agentdag.adapters.kernel.executor_claude import OAuthTokenFile
from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.domain.kernel_errors import KernelError, SpecRejected
from agentdag.domain.models import Budget, Isolation, Kind, NodeSpec, TierRole
from agentdag.domain.policy import Thresholds

if TYPE_CHECKING:
    from typing import Any


def shipped() -> Path:
    """Return the path of the shipped policy YAML, resolved via ``importlib.resources``."""
    return Path(str(files("agentdag.policy") / "tier-policy.yaml"))


def work(**over: Any) -> NodeSpec:
    """Build a minimal ``work`` node spec, overriding any field the caller names."""
    base: dict[str, Any] = {
        "node_id": "w",
        "kind": Kind.WORK,
        "executor": "claude",
        "tier_role": TierRole.STANDARD,
        "isolation": Isolation.WORKTREE,
        "deadline_s": 60,
        "budget": Budget(),
    }
    base.update(over)
    return NodeSpec.model_validate(base)


@pytest.mark.os_agnostic
def test_shipped_policy_loads_and_is_versioned_by_content() -> None:
    """The shipped table loads, is content-versioned, and carries the run limits and thresholds."""
    p = load_policy(shipped())
    assert p.version.startswith("sha256:")
    assert p.run_limits.tokens_per_row["sonnet"] == 8_000_000
    assert p.rows["sonnet"].handover_at_tokens == 100_000
    assert p.thresholds.max_continuations == 3
    assert p.run_limits.deadline_ceiling_s == 5400  # RunLimits.deadline_ceiling_s (design 2.3 rule 4, M3)


@pytest.mark.os_agnostic
def test_the_node_granularity_floor_is_counted_in_tokens_not_minutes() -> None:
    """The floor is 260,000 tokens, and the superseded minutes key is refused rather than ignored.

    Two assertions with two different jobs. The first pins the shipped value. The second pins the
    UNIT independently of that value, so it still bites if the figure is re-derived: ``Thresholds``
    forbids extra keys, so a table carrying the superseded ``min_node_minutes`` ALONGSIDE a valid
    ``min_node_tokens`` still raises. Supplying both is what isolates the mechanism: with only the
    stale key the model would reject it for the required field being absent instead, and the
    assertion would pass without ever exercising the extra-key rule it is named for.
    """
    p = load_policy(shipped())
    assert p.thresholds.min_node_tokens == 260_000

    with pytest.raises(ValidationError):
        Thresholds.model_validate(
            {
                "min_node_tokens": 260_000,
                "min_node_minutes": 0.5,
                "reduce_tree_fanin": 12,
                "journal_max_lines": 5000,
                "max_continuations": 3,
            }
        )


@pytest.mark.os_agnostic
def test_role_resolves_to_the_cheapest_available_row_and_a_model_override_is_checked() -> None:
    """No model: cheapest available row by rank. A model override: must exist, be available, list the role."""
    p = load_policy(shipped())
    # rank 20 (sonnet) is the cheapest of the rows listing "standard".
    assert p.resolve(work()).alias == "sonnet"
    # rank 30 (opus) is the cheapest AVAILABLE row listing "deep": rank 25 (codex) is cheaper
    # and lists it, and ships `available: false` because nothing wires its executor.
    assert p.resolve(work(tier_role=TierRole.DEEP)).alias == "opus"
    # An explicit model override that lists the requested role wins over the cheapest-row rule.
    assert p.resolve(work(model="opus", tier_role=TierRole.DEEP)).alias == "opus"
    # opus lists only "deep", not the default "standard" role work() carries.
    with pytest.raises(SpecRejected):
        p.resolve(work(model="opus"))
    # No row named "nonesuch" exists at all.
    with pytest.raises(SpecRejected):
        p.resolve(work(model="nonesuch"))


_SONNET_AVAILABLE = "alias: sonnet\n    executor: claude\n    rank: 20\n    cost_class: mid\n    available: true"
_SONNET_UNAVAILABLE = _SONNET_AVAILABLE.replace("available: true", "available: false")
_CODEX_UNAVAILABLE = "    # wire_kernel refuses a table advertising a row it cannot dispatch.\n    available: false"
_CODEX_AVAILABLE = _CODEX_UNAVAILABLE.replace("available: false", "available: true")
"""The codex row as it would read if the executor were wired.

It SHIPS unavailable: nothing wires ``mcp:codex/codex`` (the Codex arm is cut), and
``wire_kernel`` now refuses a table offering a row it cannot dispatch. These tests flip it ON
to exercise the resolution rule that needs two rows listing one role, which is what the shipped
table no longer provides on its own."""


def _unavailable_rows(text: str) -> int:
    """Count rows whose ``available`` key is false, ignoring any comment that says the words."""
    return len(re.findall(r"^\s+available:\s*false\s*$", text, re.MULTILINE))


def _flip(text: str, old: str, new: str) -> str:
    """Replace ``old`` with ``new`` and prove it actually changed something - never a silent no-op."""
    flipped = text.replace(old, new)
    assert flipped != text, f"replace target not found verbatim in the shipped YAML: {old!r}"
    return flipped


@pytest.mark.os_agnostic
def test_a_row_flipped_unavailable_is_skipped(tmp_path: Path) -> None:
    """An unavailable row is skipped in favor of the next-cheapest one, not merely ignored.

    ``standard`` is listed by two rows (sonnet, rank 20, and codex, rank 25) - flipping only
    sonnet does not exhaust the role, it demotes resolution to the next cheapest available row.
    Flipping BOTH exhausts every row listing the role, which is the ``SpecRejected`` case.

    Codex must be flipped ON first, because it now SHIPS unavailable: nothing wires its
    executor. That makes this test's premise something it has to establish rather than inherit,
    and saying so is the point - without the flip the "demoted to the next row" arm would be
    testing a table with only one row listing ``standard``, where demotion cannot happen at all.
    """
    text = _flip(shipped().read_text(), _CODEX_UNAVAILABLE, _CODEX_AVAILABLE)

    one_down = _flip(text, _SONNET_AVAILABLE, _SONNET_UNAVAILABLE)
    # Count the indented KEY, never the bare substring: the shipped table explains the codex
    # row's state in a comment that contains the words "available: false", so a substring count
    # reads three where two rows are flipped.
    assert _unavailable_rows(one_down) == 1
    one_path = tmp_path / "sonnet-unavailable.yaml"
    one_path.write_text(one_down)
    assert load_policy(one_path).resolve(work()).alias == "codex"

    both_down = _flip(one_down, _CODEX_AVAILABLE, _CODEX_UNAVAILABLE)
    assert _unavailable_rows(both_down) == 2
    both_path = tmp_path / "sonnet-and-codex-unavailable.yaml"
    both_path.write_text(both_down)
    with pytest.raises(SpecRejected):
        load_policy(both_path).resolve(work())


@pytest.mark.os_agnostic
def test_wiring_refuses_a_table_offering_a_row_no_executor_can_run(tmp_path: Path) -> None:
    """A row that cannot be dispatched must not be offered to a planner.

    The policy table and the executor map are two halves of one decision and nothing compared
    them. Measured 2026-09-02: the shipped table carried `codex` at `available: true` with
    `executor: "mcp:codex/codex"`, left behind when the Codex arm was cut, and three nodes in
    one run resolved to it and died at dispatch - each after a planner had been paid to write
    the plan naming them. The cost of finding out at dispatch is one wasted plan per node; the
    cost of finding out here is nothing.
    """
    from agentdag.composition.kernel import wire_kernel

    revived = _flip(shipped().read_text(), _CODEX_UNAVAILABLE, _CODEX_AVAILABLE)
    path = tmp_path / "codex-revived.yaml"
    path.write_text(revived)

    with pytest.raises(KernelError, match="not wired") as caught:
        wire_kernel(
            policy_path=path,
            credential=OAuthTokenFile(_tokenfile(tmp_path)),
            parallel=1,
            max_turns=25,
            deny_bash=(),
            notifier=NoNotifier(),
        )
    assert "codex" in str(caught.value), "the message must name the row, not just the count"
    assert "mcp:codex/codex" in str(caught.value), "and the executor it asked for"

    # The control: the table AS SHIPPED wires cleanly, so the refusal is about this row and not
    # about the check rejecting every table it is handed.
    wire_kernel(
        policy_path=shipped(),
        credential=OAuthTokenFile(_tokenfile(tmp_path)),
        parallel=1,
        max_turns=25,
        deny_bash=(),
        notifier=NoNotifier(),
    )


def _tokenfile(tmp_path: Path) -> Path:
    """A credential file with the right shape; nothing here ever dispatches."""
    path = tmp_path / "token"
    path.write_text("sk-ant-oat01-NOT-A-REAL-TOKEN\n")
    return path
