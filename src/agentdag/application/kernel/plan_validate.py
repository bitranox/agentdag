"""Plan validation by absence: a plan entry naming an unregistered op refuses the whole plan.

Task 29 shipped :class:`~agentdag.domain.plan.Plan`/:class:`~agentdag.domain.plan.Entry` as
pure shape - "it knows nothing about which ops exist ... or how a plan gets validated against
one" (that module's own docstring). This module is the "how". The rules:

* every entry's op must be one the composition root registered
  (:class:`~agentdag.application.kernel.registry.OpRegistry`);
* no two entries may carry the same ``node_id``, which reallocation would collapse into one;
* an entry's args must validate against its op's own model;
* every ``FieldRef`` in a condition must name either an entry of this plan - and then a field
  that entry's op can emit, widened by
  :data:`~agentdag.domain.condition.RESERVED_TOP_LEVEL_FIELDS` - or an already-admitted node
  of ``graph``;
* every dep must name an already-admitted node or an earlier entry in this same plan;
* the plan must not carry more entries than policy allows;
* for a ROOT plan only, ``done_when`` must be able to settle True AT ALL - a tree that no
  records can satisfy admits a run that can only ever go to its limits;
* and, for a ROOT plan only, ``done_when`` must not settle True over the records a run that
  accomplished NOTHING would hold (decision 4): a gate or a scan re-runs a check, and a check
  that reads the same before and after cannot tell finished from never-started.

Whole or nothing (decision 1): every rule runs over every entry, and :class:`Refused` carries
every reason found, never just the first - a planner re-run on one refusal fixes what it can
see, not what stopped the loop that found the first one.

Contents:
    * :class:`Accepted` - the plan, its entries' node ids reallocated by the coordinator.
    * :class:`Refused` - every reason to refuse, in rule order.
    * :func:`validate_plan` - the whole-or-nothing verdict.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ...domain.condition import (
    RESERVED_TOP_LEVEL_FIELDS,
    AllOf,
    AnyOf,
    Compare,
    FieldRef,
    Not,
    evaluate,
    referenced_fields,
)
from ...domain.models import NodeStatus, ResultRecord
from .registry import UnregisteredOpError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ...domain.condition import Condition
    from ...domain.models import NodeSpec
    from ...domain.plan import Entry, Plan
    from ...domain.policy import RunLimits
    from .registry import OpRegistry, OpSpec

__all__ = ["Accepted", "Refused", "validate_plan"]


@dataclass(frozen=True, slots=True)
class Accepted:
    """A plan that passed every rule, its entries' node ids ALLOCATED by the coordinator.

    Never the planner's own ``spec.node_id`` values (:func:`validate_plan`'s own rule: a
    planner-authored id is untrusted input, ``"evil/../key"`` included, and is never used to
    name a node directory). ``allocate_id`` supplies every id in this plan, and every cross-
    reference into it - a dep, a ``FieldRef`` in ``holds_while``/``done_when``/an entry's own
    ``acceptance`` - is rewritten to match, so ``plan`` is internally consistent under its NEW
    ids, not merely renamed at the leaves while everything that pointed at the old one dangles.
    """

    plan: Plan


@dataclass(frozen=True, slots=True)
class Refused:
    """Every reason ``plan`` was refused, in rule order - never just the first (decision 1)."""

    reasons: tuple[str, ...]


def validate_plan(
    plan: Plan,
    *,
    registry: OpRegistry,
    graph: Mapping[str, NodeSpec],
    limits: RunLimits,
    is_root: bool,
    allocate_id: Callable[[], str],
) -> Accepted | Refused:
    """Refuse ``plan`` whole, or accept it with every node id reallocated.

    Args:
        plan: The planner-emitted plan to validate.
        registry: What op names are known, and what each one promises.
        graph: Already-admitted node ids to their specs - the ONLY outside deps an entry's
            own ``spec.deps`` may name (besides an earlier entry in this same plan).
        limits: The run's ceilings, including ``max_nodes_per_plan``.
        is_root: Whether ``plan`` is the run's own top-level plan, whose ``done_when`` must
            be able to settle True at all AND must not be settleable without a record from a
            state-changing op. ``False`` for a nested plan a ``plan`` op dispatches, which a
            parent judges instead - a nested plan's ``done_when`` is not what ends the run.
        allocate_id: Mints one fresh node id per accepted entry; called only on acceptance,
            never while a plan is still being checked.

    Returns:
        :class:`Refused` with every reason found, or :class:`Accepted` with every entry's
        node id - and every reference to it - rewritten to a freshly allocated one.
    """
    reasons: list[str] = [
        *_op_reasons(plan, registry),
        *_duplicate_id_reasons(plan),
        *_args_reasons(plan, registry),
        *_contract_reasons(plan, registry, graph),
        *_dep_reasons(plan, graph),
        *_size_reasons(plan, limits),
    ]
    if is_root:
        reasons.extend(_root_settleable_reasons(plan))
        reasons.extend(_root_state_change_reasons(plan, registry))
    if reasons:
        return Refused(reasons=tuple(reasons))
    return Accepted(plan=_reallocated(plan, allocate_id))


def _op_reasons(plan: Plan, registry: OpRegistry) -> list[str]:
    """Return one reason per entry naming an op nothing registered."""
    return [
        f"entry {e.spec.node_id!r} names unregistered op {e.op!r}"
        for e in plan.entries
        if _op_or_none(e, registry) is None
    ]


def _duplicate_id_reasons(plan: Plan) -> list[str]:
    """Return one reason per node id two or more entries share.

    Every id an entry carries is discarded and reallocated (:func:`_reallocated`), but the
    old id is what the mapping from old to new is KEYED by, so two entries sharing one
    collapse onto a single allocated id: two nodes with one identity, one journal key and
    one node directory, reported by nothing. Refusing is the only answer that keeps
    ``allocate_id`` total - a plan cannot say which of the two a dep or a ``FieldRef``
    naming that id meant.
    """
    counts = Counter(e.spec.node_id for e in plan.entries)
    return [
        f"plan carries {count} entries with the same node id {node_id!r}; entry ids must be unique"
        for node_id, count in sorted(counts.items())
        if count > 1
    ]


def _args_reasons(plan: Plan, registry: OpRegistry) -> list[str]:
    """Return one reason per entry whose ``args`` fail its own op's args model.

    Skips an entry whose op is unregistered: :func:`_op_reasons` already reports that, and
    there is no model here to validate against.
    """
    reasons: list[str] = []
    for e in plan.entries:
        op = _op_or_none(e, registry)
        if op is None:
            continue
        try:
            op.args_model.model_validate(dict(e.args))
        except ValidationError as exc:
            reasons.append(f"entry {e.spec.node_id!r} args invalid for op {e.op!r}: {exc}")
    return reasons


def _contract_reasons(plan: Plan, registry: OpRegistry, graph: Mapping[str, NodeSpec]) -> list[str]:
    """Return one reason per ``FieldRef`` a condition names outside its entry's op contract."""
    entries_by_id = {e.spec.node_id: e for e in plan.entries}
    reasons: list[str] = []
    for cond in _every_condition(plan):
        for ref in referenced_fields(cond):
            reasons.extend(_field_ref_reasons(ref, entries_by_id, registry, graph))
    return reasons


def _field_ref_reasons(
    ref: FieldRef, entries_by_id: Mapping[str, Entry], registry: OpRegistry, graph: Mapping[str, NodeSpec]
) -> list[str]:
    """Return the one reason ``ref`` names something no record can answer, if any.

    Three cases, in order:

    * an entry of THIS plan - checked against its op's ``output_contract`` widened by
      :data:`~agentdag.domain.condition.RESERVED_TOP_LEVEL_FIELDS`, which is exactly the
      set :func:`~agentdag.domain.condition.referenceable_view` will make readable;
    * an ALREADY-ADMITTED node of ``graph`` - permitted, the same way :func:`_dep_reasons`
      already permits a dep naming one. ``graph`` holds
      :class:`~agentdag.domain.models.NodeSpec` values, which carry no op name, so there is
      no contract here to check against - the record either carries the field at run time
      or the condition reads as undecided, which the three-valued evaluator reports;
    * neither - refused: nothing in this run will ever produce a record under that id.
    """
    target = entries_by_id.get(ref.entry)
    if target is None:
        if ref.entry in graph:
            return []
        return [f"condition references entry {ref.entry!r}, which names no admitted node or entry of this plan"]
    op = _op_or_none(target, registry)
    if op is None:
        return []  # its op is unregistered; _op_reasons already reports that
    if ref.field not in (op.output_contract | RESERVED_TOP_LEVEL_FIELDS):
        return [f"condition references {ref.entry}.{ref.field}, not in op {target.op!r}'s output contract"]
    return []


def _every_condition(plan: Plan) -> list[Condition]:
    """Return every condition this plan carries: ``holds_while``, ``done_when``, each acceptance."""
    conditions: list[Condition] = [plan.done_when]
    if plan.holds_while is not None:
        conditions.append(plan.holds_while)
    conditions.extend(e.acceptance for e in plan.entries if e.acceptance is not None)
    return conditions


def _dep_reasons(plan: Plan, graph: Mapping[str, NodeSpec]) -> list[str]:
    """Return one reason per dep naming neither an admitted graph node nor an earlier entry."""
    reasons: list[str] = []
    seen: set[str] = set(graph)
    for e in plan.entries:
        reasons.extend(
            f"entry {e.spec.node_id!r} depends on {dep!r}, which names no admitted node or earlier entry"
            for dep in e.spec.deps
            if dep not in seen
        )
        seen.add(e.spec.node_id)
    return reasons


def _size_reasons(plan: Plan, limits: RunLimits) -> list[str]:
    """Return the one reason ``plan`` carries more entries than policy allows, if any."""
    count = len(plan.entries)
    if count > limits.max_nodes_per_plan:
        return [f"plan has {count} entries, over the max_nodes_per_plan limit of {limits.max_nodes_per_plan}"]
    return []


def _root_settleable_reasons(plan: Plan) -> list[str]:
    """Return the reason a ROOT plan's ``done_when`` can never settle True, if any.

    Admission is the only place this is cheap to catch: a plan whose ``done_when`` no set of
    records can satisfy is accepted, dispatched, and then runs until some LIMIT stops it,
    with nothing anywhere saying why it never finished. Refusing it names the defect while
    the planner is still there to fix it.
    """
    if _can_settle_true(plan.done_when):
        return []
    return [
        "done_when can never settle True, whatever records the run produces; a root plan that "
        "cannot complete would run to its limits instead of finishing"
    ]


def _can_settle_true(cond: Condition) -> bool:
    """Return whether SOME assignment of records makes ``cond`` evaluate True.

    Structural only, and deliberately so: a :class:`~agentdag.domain.condition.Compare` is
    taken to be able to go either way, so a contradiction BETWEEN leaves
    (``AllOf(x == 1, x == 2)``) is not detected here - that needs a solver over the value
    domain, while what this refuses needs nothing but the shape. The two shapes that are
    decided by the shape alone are the empty groups the evaluator itself defines:
    ``AnyOf(any=())`` is vacuously False and ``AllOf(all=())`` is vacuously True
    (:func:`~agentdag.domain.condition.evaluate`), so a tree that reduces to the first can
    never say done.

    Recursion, one line per shape, with :func:`_can_settle_false` as its dual so a
    :class:`~agentdag.domain.condition.Not` is carried through rather than special-cased:

    * an :class:`~agentdag.domain.condition.AllOf` settles True only with EVERY conjunct
      True, so every child must be able to; ``all(())`` is True, matching the vacuous truth
      of the empty conjunction;
    * an :class:`~agentdag.domain.condition.AnyOf` settles True on ANY branch, so one child
      that can is enough; ``any(())`` is False, which is exactly the case this rule exists
      for - and it stays False however deeply that empty group is nested, because a parent
      only reports True through children that can;
    * a :class:`~agentdag.domain.condition.Not` settles True exactly when its child settles
      False.
    """
    if isinstance(cond, Compare):
        return True
    if isinstance(cond, Not):
        return _can_settle_false(cond.not_)
    if isinstance(cond, AllOf):
        return all(_can_settle_true(child) for child in cond.all)
    return any(_can_settle_true(child) for child in cond.any)


def _can_settle_false(cond: Condition) -> bool:
    """Return whether SOME assignment of records makes ``cond`` evaluate False.

    The dual of :func:`_can_settle_true`, needed only to decide a
    :class:`~agentdag.domain.condition.Not`: an ``AllOf`` settles False as soon as ONE
    conjunct does, an ``AnyOf`` only when EVERY branch does, and the empty groups again fall
    out of ``any(())``/``all(())`` - an empty ``AllOf`` can never be False, so ``Not`` of it
    can never be True.
    """
    if isinstance(cond, Compare):
        return True
    if isinstance(cond, Not):
        return _can_settle_true(cond.not_)
    if isinstance(cond, AllOf):
        return any(_can_settle_false(child) for child in cond.all)
    return all(_can_settle_false(child) for child in cond.any)


def _root_state_change_reasons(plan: Plan, registry: OpRegistry) -> list[str]:
    """Return the reason a ROOT plan's ``done_when`` can settle over a run that did nothing.

    Decision 4. A gate or a scan re-runs a check; neither is ever itself the reason a run is
    done, because a check that reads the same before and after cannot tell finished from
    never-started. The question is not whether ``done_when`` MENTIONS an op whose records
    could evidence work - that is satisfied by ``AnyOf(gate.rc == 0, judge.verdict ==
    "pass")``, which then settles on the gate alone - but whether it can settle WITHOUT any.

    The test is direct: build the records a run that accomplished NOTHING would hold
    (:func:`_no_work_records`) and settle ``done_when`` over them with the very evaluator the
    dispatcher uses (:func:`~agentdag.domain.condition.evaluate`). True means this plan calls
    itself done having done nothing, so it is refused. Sharing the evaluator is the point:
    the rule cannot drift from the semantics it is guarding, which is what a second,
    hand-rolled walk over the condition grammar allowed. That walk asked its question per OP,
    and a per-op answer cannot see that ``reduce:count``'s ``count == 0`` is the do-nothing
    value while ``count >= 1`` is evidence - both name the same op.

    ``None`` (undecided) is not True and so does not refuse: an undecided ``done_when`` never
    completes a run either, so a plan that cannot settle over the do-nothing records is
    exactly a plan needing something to happen first.
    """
    if evaluate(_conservative(plan.done_when, plan), _no_work_records(plan, registry)) is True:
        return [
            "done_when settles True over the records a run that accomplished nothing would hold; "
            "a root plan whose levers cannot change state cannot tell finished from never-started"
        ]
    return []


def _no_work_records(plan: Plan, registry: OpRegistry) -> dict[str, ResultRecord]:
    """Build, per entry, the record it would carry had the run accomplished nothing.

    The scenario modelled is "every node that WOULD run in a
        do-nothing run ran, and nothing was accomplished".

        An op declaring :attr:`~agentdag.application.kernel.registry.OpSpec.facts_if_no_work`
        contributes a record holding exactly those facts: a gate, a scan and a reduce all still
        RUN in such a run, and their readings are what they would have been anyway. An op
        declaring ``None`` contributes NO record, because running it IS the accomplishment -
        dispatching ``work``, answering an ``approve``, expanding a ``plan`` - and its absence is
        then read three-valued, so a comparison on it goes UNDECIDED rather than False.

        That distinction is load-bearing in both directions:
        ``AnyOf(gate.rc == 0, work.turns >= 1)`` settles True on the gate branch alone and is
        refused, while ``AllOf`` of the same two is undecided and accepted - which is exactly the
        shape decision 4 asks a planner to write.

        ``status`` is DONE on every synthesized record. A node reaches DONE whether or not it
        achieved anything, so DONE is the honest do-nothing value, and a root ``done_when`` that
        settles on a status alone is refused for that reason.
    """
    records: dict[str, ResultRecord] = {}
    for entry in plan.entries:
        op = _op_or_none(entry, registry)
        if op is None or op.facts_if_no_work is None:
            continue
        records[entry.spec.node_id] = ResultRecord(
            node_id=entry.spec.node_id,
            attempt=0,
            status=NodeStatus.DONE,
            key_facts=dict(op.facts_if_no_work),
            executor_used="-",
            model_used="-",
            effort_used="-",
            duration_s=0.0,
            input_hash="sha256:0",
        )
    return records


_ALWAYS_TRUE = AllOf(all=())
"""A condition that settles True with no children, per Kleene's vacuous AND.

Used to stand in for a comparison this rule must treat as satisfiable without work.
"""


def _conservative(cond: Condition, plan: Plan) -> Condition:
    """Replace every comparison on an entry THIS plan does not own with an always-True leaf.

    A ``done_when`` may reference an already-admitted graph node, whose op this plan never
    names, so nothing here can say what it reports on a do-nothing run. Leaving it absent
    would make its comparison undecided, which is the LOOSER direction - it would let a root
    plan pass by leaning on a node whose behaviour this rule cannot see. Assuming instead
    that such a node is satisfiable without work keeps the refusal where it was before this
    rule was rewritten: conservative, and fixed by conjoining an entry of the plan's own.
    """
    owned = {e.spec.node_id for e in plan.entries}
    if isinstance(cond, Compare):
        return cond if cond.ref.entry in owned else _ALWAYS_TRUE
    if isinstance(cond, Not):
        return Not(not_=_conservative(cond.not_, plan))
    if isinstance(cond, AllOf):
        return AllOf(all=tuple(_conservative(child, plan) for child in cond.all))
    return AnyOf(any=tuple(_conservative(child, plan) for child in cond.any))


def _op_or_none(entry: Entry, registry: OpRegistry) -> OpSpec | None:
    """Return ``entry``'s registered op, or ``None`` when it names none - never raises."""
    try:
        return registry.get(entry.op)
    except UnregisteredOpError:
        return None


def _reallocated(plan: Plan, allocate_id: Callable[[], str]) -> Plan:
    """Return ``plan`` with every entry's node id freshly allocated, and every reference rewritten.

    Four kinds of cross-reference exist and all four are rewritten: an entry's own
    ``spec.node_id``, its ``spec.deps``, every ``FieldRef`` in ``holds_while``/``done_when``/
    an entry's own ``acceptance``, and the PLAN's own ``deps``. Missing any one leaves an
    accepted plan pointing at ids no node in it carries.

    ``mapping`` is one entry per node id and is total over ``plan.entries`` because
    :func:`_duplicate_id_reasons` has already refused a plan whose entries share one - a
    duplicate would otherwise collapse two entries onto a single allocated id.
    """
    mapping = {e.spec.node_id: allocate_id() for e in plan.entries}
    entries = tuple(_reallocated_entry(e, mapping) for e in plan.entries)
    return plan.model_copy(
        update={
            "entries": entries,
            "holds_while": _remap(plan.holds_while, mapping) if plan.holds_while is not None else None,
            "done_when": _remap(plan.done_when, mapping),
            "deps": tuple(mapping.get(dep, dep) for dep in plan.deps),
        }
    )


def _reallocated_entry(entry: Entry, mapping: Mapping[str, str]) -> Entry:
    """Return ``entry`` with its own node id, its deps and its own acceptance remapped."""
    new_id = mapping[entry.spec.node_id]
    new_deps = [mapping.get(dep, dep) for dep in entry.spec.deps]
    new_spec = entry.spec.model_copy(update={"node_id": new_id, "deps": new_deps})
    new_acceptance = _remap(entry.acceptance, mapping) if entry.acceptance is not None else None
    return entry.model_copy(update={"spec": new_spec, "acceptance": new_acceptance})


def _remap(cond: Condition, mapping: Mapping[str, str]) -> Condition:
    """Return ``cond`` with every ``FieldRef.entry`` it names rewritten through ``mapping``.

    A ``FieldRef`` naming an entry OUTSIDE this plan (an already-admitted graph node) is left
    unchanged - ``mapping`` only ever holds THIS plan's own (old id -> new id) pairs, built by
    :func:`_reallocated` from ``plan.entries`` alone.
    """
    if isinstance(cond, Compare):
        if cond.ref.entry in mapping:
            return cond.model_copy(update={"ref": FieldRef(entry=mapping[cond.ref.entry], field=cond.ref.field)})
        return cond
    if isinstance(cond, Not):
        return Not(not_=_remap(cond.not_, mapping))
    if isinstance(cond, AllOf):
        return AllOf(all=tuple(_remap(child, mapping) for child in cond.all))
    return AnyOf(any=tuple(_remap(child, mapping) for child in cond.any))
