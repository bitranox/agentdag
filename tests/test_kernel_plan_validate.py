"""RED/GREEN tests for plan validation: an entry naming an unregistered op refuses the whole plan.

``REG`` is the REAL production registry (:func:`~agentdag.composition.kernel.build_op_registry`),
not a hand-rolled shadow copy: these tests prove :func:`validate_plan`'s rules against what the
composition root actually registered for Task 30 (``work``, ``gate:make-test``, ``judge``, and
that ``plan``/``apply`` are (respectively) registered/never registered), the same way Task 31
will.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from agentdag.application.kernel.plan_validate import Accepted, Refused, validate_plan
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.condition import AllOf, Compare, FieldRef
from agentdag.domain.models import Kind, NodeSpec
from agentdag.domain.plan import Entry, Plan
from agentdag.domain.policy import RunLimits

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agentdag.domain.condition import Condition

REG = build_op_registry()

LIMITS = RunLimits(
    tokens_per_row={},
    deadline_ceiling_s=5400.0,
    per_kind_ceiling={},
    planner_kinds=[],
    top_role_budget_floor=0.0,
    max_replans=3,
    max_nodes_per_run=200,
    # A small ceiling (not the shipped 40) so the size-limit test below stays cheap; every
    # other test here uses at most two entries.
    max_nodes_per_plan=3,
)

_OP_KIND: dict[str, Kind] = {
    "work": Kind.WORK,
    "gate:make-test": Kind.GATE,
    "scan": Kind.GATE,
    "reduce:count": Kind.REDUCE,
    "approve": Kind.APPROVE,
    "plan": Kind.PLANNER,
    "judge": Kind.SYNTH,
}


def ids() -> Callable[[], str]:
    """Return a fresh id allocator: a distinct id per call, for tests that never inspect them."""
    counter = itertools.count()
    return lambda: f"n-{next(counter):04d}"


def entry(
    *,
    op: str,
    node_id: str = "n0",
    args: Mapping[str, object] | None = None,
    deps: Sequence[str] = (),
    output_contract: frozenset[str] | None = None,
) -> Entry:
    """Build a minimal entry naming ``op``, defaulting its node id to ``"n0"``."""
    spec = NodeSpec(node_id=node_id, kind=_OP_KIND.get(op, Kind.WORK), deadline_s=60, deps=list(deps))
    return Entry(
        spec=spec,
        op=op,
        args=dict(args or {}),
        brief="do it",
        output_contract=output_contract or frozenset({"status"}),
    )


def plan_with(
    *, entries: Sequence[Entry], holds_while: Condition | None = None, done_when: Condition | None = None
) -> Plan:
    """Build a plan over ``entries``, defaulting ``done_when`` to a valid reference into the first."""
    default_done = Compare(ref=FieldRef(entry=entries[0].spec.node_id, field="status"), op="==", value="passed")
    return Plan(
        goal="test",
        entries=tuple(entries),
        holds_while=holds_while,
        done_when=done_when if done_when is not None else default_done,
    )


def test_unregistered_op_is_refused_whole() -> None:
    plan = plan_with(entries=[entry(op="work"), entry(op="teleport")])
    out = validate_plan(plan, registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids())
    assert isinstance(out, Refused) and any("teleport" in r for r in out.reasons)


def test_args_are_validated_by_the_ops_model() -> None:
    out = validate_plan(
        plan_with(entries=[entry(op="gate:make-test", args={"argv": 5})]),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=False,
        allocate_id=ids(),
    )
    assert isinstance(out, Refused)


def test_condition_may_reference_only_declared_contract_fields() -> None:
    e = entry(op="work")  # contract: {"status", "artifact_ref"}
    bad = Compare(ref=FieldRef(entry="n0", field="repo_count"), op="<=", value=20)
    out = validate_plan(
        plan_with(entries=[e], holds_while=bad), registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids()
    )
    assert isinstance(out, Refused)


def test_deps_may_name_only_graph_or_earlier_entries() -> None:
    first = entry(op="work", node_id="n0")

    ok_second = entry(op="work", node_id="n1", deps=["n0"])
    ok = validate_plan(
        plan_with(entries=[first, ok_second]), registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids()
    )
    assert isinstance(ok, Accepted)

    bad_second = entry(op="work", node_id="n1", deps=["n2"])  # n2 is later than n1, never admitted
    bad = validate_plan(
        plan_with(entries=[first, bad_second]),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=False,
        allocate_id=ids(),
    )
    assert isinstance(bad, Refused) and any("n2" in r for r in bad.reasons)


def test_node_ids_are_allocated_never_taken_from_the_model() -> None:
    plan = plan_with(entries=[entry(op="work", node_id="evil/../key")])
    out = validate_plan(plan, registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=lambda: "n-0001")
    assert isinstance(out, Accepted)
    assert out.plan.entries[0].spec.node_id == "n-0001"


def test_more_than_max_nodes_per_plan_is_refused() -> None:
    entries = [entry(op="work", node_id=f"n{i}") for i in range(LIMITS.max_nodes_per_plan + 1)]
    out = validate_plan(
        plan_with(entries=entries), registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids()
    )
    assert isinstance(out, Refused) and any("max_nodes_per_plan" in r for r in out.reasons)


def test_root_done_when_over_only_gate_fields_is_refused_unless_judged() -> None:  # decision 4
    gate_only = Compare(ref=FieldRef(entry="n0", field="rc"), op="==", value=0)
    out = validate_plan(
        plan_with(entries=[entry(op="gate:make-test", node_id="n0")], done_when=gate_only),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(out, Refused) and any("cannot change state" in r for r in out.reasons)

    with_judge = plan_with(
        entries=[entry(op="gate:make-test", node_id="n0"), entry(op="judge", node_id="n1")],
        done_when=AllOf(all=(gate_only, Compare(ref=FieldRef(entry="n1", field="verdict"), op="==", value="pass"))),
    )
    accepted = validate_plan(with_judge, registry=REG, graph={}, limits=LIMITS, is_root=True, allocate_id=ids())
    assert isinstance(accepted, Accepted)


def test_same_plan_not_root_is_accepted() -> None:  # the rule is a ROOT rule
    gate_only = Compare(ref=FieldRef(entry="n0", field="rc"), op="==", value=0)
    plan = plan_with(entries=[entry(op="gate:make-test", node_id="n0")], done_when=gate_only)
    out = validate_plan(plan, registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids())
    assert isinstance(out, Accepted)


def test_plan_op_is_registered_and_apply_is_not() -> None:
    assert "plan" in REG.names()
    assert "apply" not in REG.names()
