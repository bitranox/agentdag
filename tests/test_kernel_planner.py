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
from kernel_fakes import FakeScanner, OneRowPolicy, PlanWritingExecutor, fresh_run_dir, wire

from agentdag.application.kernel.planner import PLANNER_PROMPT, NotPlanned, Planned, dispatch_planner
from agentdag.application.kernel.registry import PlanContext
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.models import (
    Budget,
    ErrorType,
    Isolation,
    Kind,
    NodeError,
    NodeOutcome,
    NodeSpec,
    NodeStatus,
    TierRole,
)
from agentdag.domain.plan import PLAN_FILENAME, plan_json_schema
from agentdag.domain.policy import FailureAction, RunLimits

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


class RefusingExecutor:
    """An executor whose node was refused by the PROVIDER, writing no plan.

    The shape a dead credential or an exhausted quota produces: the dispatch returns, the
    record carries the refusal, and no ``plan.json`` exists - which is indistinguishable, to
    a reader of the directory alone, from a planner that simply wrote nothing.
    """

    def __init__(self, error_type: ErrorType) -> None:
        self.error_type = error_type
        self.requests: list[object] = []

    async def run(self, request: object) -> NodeOutcome:
        """Return a refusal outcome, having written nothing."""
        self.requests.append(request)
        return NodeOutcome(
            status=NodeStatus.REFUSED,
            artefact_refs=[],
            key_facts={},
            typed_fields=[],
            charged_tokens={"sonnet": 10},
            executor_used="claude",
            model_used="sonnet",
            effort_used="-",
            error=NodeError(type=self.error_type, message="401 OAuth access token is invalid", transient=False),
        )


class FailingRefusalPolicy(OneRowPolicy):
    """:class:`OneRowPolicy`, but FAILING on both provider refusals rather than suspending.

    ``OneRowPolicy`` suspends on a rate limit, and a suspend raises before the planner is ever
    handed a record - so a rate-limit arm under it would prove nothing about this branch.
    """

    on_auth_failure: FailureAction = FailureAction.FAIL_RUN
    on_rate_limit: FailureAction = FailureAction.FAIL_RUN


def run_refused_planner(tmp_path: Path, error_type: ErrorType) -> Planned | NotPlanned:
    """Dispatch a planner node the provider refused, under a policy that FAILS on it.

    The policy has to fail rather than suspend for BOTH refusals, or the arm proves nothing:
    a coordinator whose policy suspends raises before the planner ever gets a record back,
    which is the other half of this design and is tested where the suspend is. What is under
    test here is the half a run reaches when policy says the run should fail.
    """
    run_dir = fresh_run_dir(tmp_path)
    coordinator = wire(run_dir, RefusingExecutor(error_type), FakeScanner(), policy=FailingRefusalPolicy())
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
@pytest.mark.parametrize("error_type", [ErrorType.AUTH_FAILURE, ErrorType.RATE_LIMITED])
def test_a_planner_the_provider_refused_cannot_be_replanned(tmp_path: Path, error_type: ErrorType) -> None:
    """Both refusals bind the whole account, so the next planner is refused identically.

    Before this, the only signal was that no plan.json existed, which reads as an ordinary
    bad planner: the root ladder then re-planned four times against a dead credential, paid
    for every dispatch, and ended reporting no error at all.
    """
    out = run_refused_planner(tmp_path, error_type)
    assert isinstance(out, NotPlanned)
    assert out.replannable is False
    assert any("refused" in reason for reason in out.reasons), out.reasons


@pytest.mark.os_agnostic
def test_a_planner_that_merely_wrote_no_plan_is_still_replannable(tmp_path: Path) -> None:
    """The control: the flag must distinguish the two, not be False for every NotPlanned."""
    out = run_planner(tmp_path, None)
    assert isinstance(out, NotPlanned)
    assert out.replannable is True


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


@pytest.mark.os_agnostic
def test_a_planner_refused_for_a_spent_budget_cannot_be_replanned(tmp_path: Path) -> None:
    """A spent row ceiling refuses every next planner identically, so re-planning cannot help.

    The same shape the provider refusals above have, and it was found the same way: measured
    2026-09-11 on a real run, a row with no headroom refused the planner, the ladder saw only
    that no plan.json existed, and it spent every max_replans attempt re-dispatching into a
    ceiling that could not move - then suspended asking whether to grant more RE-PLANS, which
    is the one thing that cannot help. The budget question is the one worth asking.
    """
    out = run_refused_planner(tmp_path, ErrorType.BUDGET_EXCEEDED)
    assert isinstance(out, NotPlanned)
    assert out.replannable is False
    assert any("budget" in reason for reason in out.reasons), out.reasons
