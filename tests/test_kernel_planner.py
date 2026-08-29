"""Task 32: a planner node's ``plan.json`` becomes a validated ``Plan`` or typed reasons.

Every arm drives :func:`~agentdag.application.kernel.planner.dispatch_planner` against a REAL
coordinator over a real run directory, with a fake executor that writes what a planner node
would write. The seam is "a node ran and left a file behind", so the double is the executor -
never a patch of the parse or of the validator, both of which are the things under test.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from kernel_fakes import FakeScanner, PlanWritingExecutor, fresh_run_dir, wire

from agentdag.application.kernel.planner import PLANNER_PROMPT, NotPlanned, Planned, dispatch_planner
from agentdag.application.kernel.registry import PlanContext
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.models import Budget, Isolation, Kind, NodeSpec, TierRole
from agentdag.domain.plan import PLAN_FILENAME, plan_json_schema
from agentdag.domain.policy import RunLimits

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

REG = build_op_registry()

LIMITS = RunLimits(
    tokens_per_row={"sonnet": 1_000_000_000},
    deadline_ceiling_s=999_999.0,
    per_kind_ceiling={},
    planner_kinds=[],
    top_role_budget_floor=0.0,
    max_replans=3,
    max_nodes_per_run=1000,
    max_nodes_per_plan=1000,
    max_plan_depth=5,
)


def planner_spec() -> NodeSpec:
    """The planner node these tests dispatch."""
    return NodeSpec(
        node_id="p_root",
        kind=Kind.PLANNER,
        tier_role=TierRole.STANDARD,
        isolation=Isolation.WORKTREE,
        write_set=["wt/a/**"],
        deadline_s=3600,
        budget=Budget(tokens={"sonnet": 400_000}),
    )


def ids() -> Callable[[], str]:
    """Allocate the node ids the coordinator hands out, n-0001 upward."""
    counter = iter(range(1, 10_000))

    def allocate() -> str:
        return f"n-{next(counter):04d}"

    return allocate


def a_plan(op: str = "work") -> str:
    """One-entry plan JSON, as a planner node would write it.

    ``node_id`` is deliberately a value the coordinator must OVERWRITE: a planner does not
    get to choose node ids (they are the journal's identity), so the arm that reads it back
    proves allocation rather than passthrough.
    """
    return json.dumps(
        {
            "goal": "g",
            "entries": [
                {
                    "spec": {
                        "node_id": "chosen-by-the-model",
                        "kind": "work",
                        "tier_role": "standard",
                        "deadline_s": 60.0,
                    },
                    "op": op,
                    "args": {},
                    "brief": "b",
                    "output_contract": ["turns"],
                    "acceptance": None,
                }
            ],
            # references the entry by the id the PLANNER used; allocation remaps it
            "done_when": {"ref": {"entry": "chosen-by-the-model", "field": "turns"}, "op": ">=", "value": 1},
        }
    )


def run_planner(tmp_path: Path, raw: str | None) -> Planned | NotPlanned:
    """Dispatch a planner node whose executor writes ``raw`` (or nothing) as its plan."""
    run_dir = fresh_run_dir(tmp_path)
    coordinator = wire(run_dir, PlanWritingExecutor(raw), FakeScanner())
    ctx = PlanContext(co=coordinator, cwd=run_dir.worktree("a"))
    return asyncio.run(
        dispatch_planner(
            spec=planner_spec(),
            goal="g",
            evidence={},
            ctx=ctx,
            registry=REG,
            limits=LIMITS,
            graph={},
            is_root=False,
            allocate_id=ids(),
        )
    )


@pytest.mark.os_agnostic
def test_a_valid_plan_json_is_parsed_validated_and_ids_allocated(tmp_path: Path) -> None:
    out = run_planner(tmp_path, a_plan())
    assert isinstance(out, Planned)
    assert out.plan.entries[0].spec.node_id == "n-0001"  # ALLOCATED, not the model's word
    # and the plan's own cross-reference followed it. Without this, allocation would leave
    # done_when pointing at an id no entry carries any more, and the plan would be accepted
    # with a condition that can never settle.
    assert out.plan.done_when.ref.entry == "n-0001"  # type: ignore[union-attr]


@pytest.mark.os_agnostic
def test_unparseable_json_is_not_planned(tmp_path: Path) -> None:
    out = run_planner(tmp_path, "{not json")
    assert isinstance(out, NotPlanned)
    assert any("parse" in r for r in out.reasons)


@pytest.mark.os_agnostic
def test_a_missing_plan_file_is_not_planned(tmp_path: Path) -> None:
    """A planner that wrote nothing is a REPORT, not a crash: the caller branches on it."""
    out = run_planner(tmp_path, None)
    assert isinstance(out, NotPlanned)
    assert any(PLAN_FILENAME in r for r in out.reasons)


@pytest.mark.os_agnostic
def test_validate_plan_reasons_are_carried_verbatim(tmp_path: Path) -> None:
    """The one that matters: a refusal reaches the caller as the VALIDATOR's own reasons.

    A flattened "planning failed" would leave the parent plan, and the re-planning path in
    Task 35, with nothing to brief the next planner with.
    """
    out = run_planner(tmp_path, a_plan(op="teleport"))
    assert isinstance(out, NotPlanned)
    assert any("teleport" in r for r in out.reasons)


@pytest.mark.os_agnostic
def test_a_not_planned_still_carries_the_planner_s_own_record(tmp_path: Path) -> None:
    """Every failure shape keeps the record. Without it the run has no journal evidence that
    a planner was dispatched at all, and the tokens it spent would be unattributable."""
    out = run_planner(tmp_path, "{not json")
    assert isinstance(out, NotPlanned)
    assert out.record.node_id == "p_root"


@pytest.mark.os_agnostic
def test_the_prompt_names_the_registered_ops_and_the_schema() -> None:
    text = PLANNER_PROMPT.format(schema=json.dumps(plan_json_schema()), ops=sorted(REG.names()))
    assert "gate:make-test" in text
    assert "'apply'" not in text  # never registered: DECISIONS item 8
    assert "done_when" in text


@pytest.mark.os_agnostic
def test_the_planner_prompt_says_judging_is_not_available_yet() -> None:
    """Refusal by absence is the backstop, not the interface. A planner that emits a judge
    gets "unregistered op", which reads as a typo unless it was told."""
    assert "judge" in PLANNER_PROMPT
    assert "not yet available" in PLANNER_PROMPT
