"""RED/GREEN tests for plan validation: an entry naming an unregistered op refuses the whole plan.

``REG`` is the REAL production registry (:func:`~agentdag.composition.kernel.build_op_registry`),
not a hand-rolled shadow copy: these tests prove :func:`validate_plan`'s rules against what the
composition root actually registered for Task 30 (``work``, ``gate:make-test``, and
that ``plan``/``apply`` are (respectively) registered/never registered), the same way Task 31
will.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from agentdag.application.kernel.plan_validate import Accepted, Refused, validate_plan
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.condition import AllOf, AnyOf, Compare, FieldRef, Not
from agentdag.domain.models import Kind, NodeSpec, NodeStatus
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
    *,
    entries: Sequence[Entry],
    holds_while: Condition | None = None,
    done_when: Condition | None = None,
    deps: Sequence[str] = (),
) -> Plan:
    """Build a plan over ``entries``, defaulting ``done_when`` to a valid reference into the first.

    The default compares ``status`` against ``NodeStatus.DONE.value`` - the enum's own value,
    never a bare literal and never the member itself, so the comparison never depends on how
    the enum renders. ``"passed"`` is not a ``NodeStatus`` member at all.
    """
    default_done = Compare(
        ref=FieldRef(entry=entries[0].spec.node_id, field="status"), op="==", value=NodeStatus.DONE.value
    )
    return Plan(
        goal="test",
        entries=tuple(entries),
        holds_while=holds_while,
        done_when=done_when if done_when is not None else default_done,
        deps=tuple(deps),
    )


def test_unregistered_op_is_refused_whole() -> None:
    plan = plan_with(entries=[entry(op="work", node_id="n0"), entry(op="teleport", node_id="n1")])
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
    e = entry(op="work")  # contract: what executor_claude's outcome constructors emit
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


def test_root_done_when_over_only_gate_fields_is_refused_unless_a_state_changer_is_named() -> None:
    """Decision 4. The rule reads an entry's ``can_change_state`` FLAG and names no op.

    This test used a ``judge`` entry purely because it was a convenient True-flagged one, and
    its old name (``..._unless_judged``) said the rule was about judging. It is not, and that
    reading cost a wrong entry in the build plan on 2026-08-29. ``work`` carries the same flag
    and makes the test say what the rule does.
    """
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

    with_state_changer = plan_with(
        entries=[entry(op="gate:make-test", node_id="n0"), entry(op="work", node_id="n1")],
        done_when=AllOf(all=(gate_only, Compare(ref=FieldRef(entry="n1", field="turns"), op=">=", value=1))),
    )
    accepted = validate_plan(with_state_changer, registry=REG, graph={}, limits=LIMITS, is_root=True, allocate_id=ids())
    assert isinstance(accepted, Accepted)


def test_same_plan_not_root_is_accepted() -> None:  # the rule is a ROOT rule
    gate_only = Compare(ref=FieldRef(entry="n0", field="rc"), op="==", value=0)
    plan = plan_with(entries=[entry(op="gate:make-test", node_id="n0")], done_when=gate_only)
    out = validate_plan(plan, registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids())
    assert isinstance(out, Accepted)


def test_plan_op_is_registered_and_apply_is_not() -> None:
    assert "plan" in REG.names()
    assert "apply" not in REG.names()


def test_duplicate_entry_node_ids_are_refused_naming_the_duplicate() -> None:
    """IMPORTANT 3: two entries sharing a node id collapse onto ONE allocated id.

    ``{e.spec.node_id: allocate_id() for e in plan.entries}`` mints one id per entry but
    keys the mapping by the planner's id, so a repeated id keeps only the LAST mint and
    both entries come out sharing it - two nodes, one identity, and nothing said so.
    """
    plan = plan_with(entries=[entry(op="work", node_id="dup"), entry(op="work", node_id="dup")])
    out = validate_plan(plan, registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids())
    assert isinstance(out, Refused)
    assert any("dup" in r for r in out.reasons)


def test_an_accepted_plan_carries_no_pre_allocation_id_anywhere() -> None:
    """IMPORTANT 4: ``Plan.deps`` is a cross-reference too, and was left un-remapped."""
    first = entry(op="work", node_id="old-a")
    second = entry(op="work", node_id="old-b", deps=["old-a"])
    plan = plan_with(
        entries=[first, second],
        holds_while=Compare(ref=FieldRef(entry="old-a", field="turns"), op=">", value=0),
        done_when=Compare(ref=FieldRef(entry="old-b", field="status"), op="==", value=NodeStatus.DONE.value),
        deps=["old-a", "old-b"],
    )
    out = validate_plan(plan, registry=REG, graph={}, limits=LIMITS, is_root=False, allocate_id=ids())
    assert isinstance(out, Accepted)
    rendered = out.plan.model_dump_json()
    assert "old-a" not in rendered
    assert "old-b" not in rendered
    assert set(out.plan.deps) == {e.spec.node_id for e in out.plan.entries}


def test_a_condition_may_reference_an_already_admitted_graph_node() -> None:
    """IMPORTANT 5: ``_remap``'s own docstring describes this case; the check refused it."""
    admitted = NodeSpec(node_id="g_outer", kind=Kind.GATE, deadline_s=60)
    ref_outer = Compare(ref=FieldRef(entry="g_outer", field="rc"), op="==", value=0)
    ok = validate_plan(
        plan_with(entries=[entry(op="work", node_id="n0")], holds_while=ref_outer),
        registry=REG,
        graph={"g_outer": admitted},
        limits=LIMITS,
        is_root=False,
        allocate_id=ids(),
    )
    assert isinstance(ok, Accepted)

    ref_nowhere = Compare(ref=FieldRef(entry="ghost", field="rc"), op="==", value=0)
    bad = validate_plan(
        plan_with(entries=[entry(op="work", node_id="n0")], holds_while=ref_nowhere),
        registry=REG,
        graph={"g_outer": admitted},
        limits=LIMITS,
        is_root=False,
        allocate_id=ids(),
    )
    assert isinstance(bad, Refused) and any("ghost" in r for r in bad.reasons)


def _gate_and_state_changer() -> tuple[Compare, Compare, list[Entry]]:
    """The two leaves and the two entries every decision-4 shape below is built from.

    ``n1`` is a ``work`` entry because decision 4 turns on ``can_change_state`` alone; any
    True-flagged op does, and naming a judge here made the rule look narrower than it is.
    """
    gate = Compare(ref=FieldRef(entry="n0", field="rc"), op="==", value=0)
    changer = Compare(ref=FieldRef(entry="n1", field="turns"), op=">=", value=1)
    return gate, changer, [entry(op="gate:make-test", node_id="n0"), entry(op="work", node_id="n1")]


def test_root_done_when_that_a_gate_alone_can_settle_is_refused() -> None:
    """IMPORTANT 6: a disjunction settles on ANY branch, so a gate branch alone completes the run.

    ``AnyOf(gate.rc == 0, w.turns >= 1)`` mentions a state-changing op, which is
    all the old check asked for - and then goes True the moment the gate goes green, with
    the work entry never dispatched.
    """
    gate, changer, entries = _gate_and_state_changer()
    out = validate_plan(
        plan_with(entries=entries, done_when=AnyOf(any=(gate, changer))),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(out, Refused) and any("cannot change state" in r for r in out.reasons)


def test_root_done_when_whose_only_state_change_is_negated_is_refused() -> None:
    """IMPORTANT 6: a negation is not a lever - ``Not(w)`` holds while the work entry never runs."""
    gate, changer, entries = _gate_and_state_changer()
    out = validate_plan(
        plan_with(entries=entries, done_when=AllOf(all=(gate, Not(not_=changer)))),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(out, Refused) and any("cannot change state" in r for r in out.reasons)


def test_root_done_when_conjoining_a_gate_with_a_state_changer_is_accepted() -> None:
    """IMPORTANT 6: a conjunction needs EVERY conjunct, so one state-changing conjunct suffices."""
    gate, changer, entries = _gate_and_state_changer()
    out = validate_plan(
        plan_with(entries=entries, done_when=AllOf(all=(gate, changer))),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(out, Accepted)


def test_root_done_when_over_an_empty_group_is_decided_deliberately() -> None:
    """IMPORTANT 6, the absent case: an empty AllOf is vacuously TRUE, so it completes a run
    with no state change at all and must be refused. An empty AnyOf is vacuously FALSE - it
    can never say done - so the state-change rule has nothing to object to; MINOR 4's
    settleability rule is what refuses it, and this test now pins WHICH rule catches which,
    so a plan cannot pass both by satisfying neither.
    """
    out = validate_plan(
        plan_with(entries=[entry(op="work", node_id="n0")], done_when=AllOf(all=())),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(out, Refused) and any("cannot change state" in r for r in out.reasons)

    empty_any = validate_plan(
        plan_with(entries=[entry(op="work", node_id="n0")], done_when=AnyOf(any=())),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(empty_any, Refused)
    assert any("never settle True" in r for r in empty_any.reasons)
    assert not any("cannot change state" in r for r in empty_any.reasons)


def test_a_read_only_scan_cannot_complete_a_root_plan_on_its_own() -> None:
    """MINOR 7: a scan changes nothing, so ``scan.stray == 0`` is the same loophole as a gate."""
    scan_only = Compare(ref=FieldRef(entry="n0", field="stray"), op="==", value=0)
    out = validate_plan(
        plan_with(entries=[entry(op="scan", node_id="n0", args={"watched": "w"})], done_when=scan_only),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(out, Refused) and any("cannot change state" in r for r in out.reasons)


def test_args_of_the_wrong_type_are_refused_and_the_right_type_accepted() -> None:
    """MINOR 8: ``scan`` has a TYPED field, so this can tell a type check from extra="forbid".

    ``gate:make-test`` declares no fields at all, so ``{"argv": 5}`` and
    ``{"argv": ["make", "test"]}`` are refused identically as EXTRA keys - that test passes
    against a model which forbids everything and type-checks nothing.
    """
    bad = validate_plan(
        plan_with(entries=[entry(op="scan", node_id="n0", args={"watched": 5})]),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=False,
        allocate_id=ids(),
    )
    assert isinstance(bad, Refused) and any("watched" in r for r in bad.reasons)

    ok = validate_plan(
        plan_with(entries=[entry(op="scan", node_id="n0", args={"watched": "w_repo"})]),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=False,
        allocate_id=ids(),
    )
    assert isinstance(ok, Accepted)


def test_a_condition_on_a_plan_entrys_status_validates() -> None:
    """MINOR 9: ``plan``'s contract is empty, so only the reserved ``status`` makes it referenceable."""
    done = Compare(ref=FieldRef(entry="n0", field="status"), op="==", value=NodeStatus.DONE.value)
    out = validate_plan(
        plan_with(entries=[entry(op="plan", node_id="n0")], done_when=done),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(out, Accepted)


def test_a_condition_on_a_field_no_body_emits_is_still_refused() -> None:
    """The reserved set widens the view; it does not open it. ``artifact_ref`` is not a field."""
    bad = Compare(ref=FieldRef(entry="n0", field="artifact_ref"), op="==", value="x")
    out = validate_plan(
        plan_with(entries=[entry(op="work", node_id="n0")], holds_while=bad),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=False,
        allocate_id=ids(),
    )
    assert isinstance(out, Refused) and any("artifact_ref" in r for r in out.reasons)


def test_root_done_when_that_can_never_settle_true_is_refused() -> None:
    """MINOR 4: ``all(())`` is vacuously True, so an EMPTY ``AnyOf`` passed the root rule.

    It can never evaluate True (``evaluate(AnyOf(any=()), ...)`` is ``False`` by design), so
    the run it admits can only ever go to its limits. Both shapes the ruling names: the bare
    empty disjunction as the whole ``done_when``, and one NESTED inside an ``AllOf`` whose
    other conjunct is perfectly satisfiable - which a check special-casing the literal empty
    tuple at the top level would let straight through.
    """
    bare = validate_plan(
        plan_with(entries=[entry(op="work", node_id="n0")], done_when=AnyOf(any=())),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(bare, Refused) and any("never settle True" in r for r in bare.reasons)

    gate, _changer, entries = _gate_and_state_changer()
    nested = validate_plan(
        plan_with(entries=entries, done_when=AllOf(all=(gate, AnyOf(any=())))),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(nested, Refused) and any("never settle True" in r for r in nested.reasons)


def test_a_negated_always_true_group_can_never_settle_either() -> None:
    """MINOR 4, the dual: ``AllOf(all=())`` is always True, so ``Not`` of it is always False.

    Proves the recursion carries the negation through rather than pattern-matching one shape:
    the unsatisfiable subtree here is an EMPTY ``AllOf``, whose own bare form is refused for a
    different reason entirely (it completes a run with no state change at all).
    """
    out = validate_plan(
        plan_with(entries=[entry(op="work", node_id="n0")], done_when=Not(not_=AllOf(all=()))),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(out, Refused) and any("never settle True" in r for r in out.reasons)


def test_the_satisfiable_neighbour_of_each_unsettleable_shape_is_still_accepted() -> None:
    """MINOR 4's control: the new rule refuses the empty group, not the shape around it.

    One entry apart from the emptiness, each neighbour is the same tree with a real branch in
    place of the empty one - so a rule that simply refused every ``AnyOf``, or every nested
    group, fails here.
    """
    done = Compare(ref=FieldRef(entry="n0", field="status"), op="==", value=NodeStatus.DONE.value)
    one_branch = validate_plan(
        plan_with(entries=[entry(op="work", node_id="n0")], done_when=AnyOf(any=(done,))),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(one_branch, Accepted)

    gate, changer, entries = _gate_and_state_changer()
    nested = validate_plan(
        plan_with(entries=entries, done_when=AllOf(all=(gate, AnyOf(any=(changer,))))),
        registry=REG,
        graph={},
        limits=LIMITS,
        is_root=True,
        allocate_id=ids(),
    )
    assert isinstance(nested, Accepted)
