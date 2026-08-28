"""Plan validation by absence: a plan entry naming an unregistered op refuses the whole plan.

Task 29 shipped :class:`~agentdag.domain.plan.Plan`/:class:`~agentdag.domain.plan.Entry` as
pure shape - "it knows nothing about which ops exist ... or how a plan gets validated against
one" (that module's own docstring). This module is the "how": every entry's op must be one
the composition root registered (:class:`~agentdag.application.kernel.registry.OpRegistry`),
its args must validate against that op's own model, every condition in the plan may reference
only a field the referenced entry's op can actually emit, every dep must name an already-
admitted node or an earlier entry in this same plan, the plan must not carry more entries than
policy allows, and - for a ROOT plan only - ``done_when`` must be decidable from at least one
entry whose op can actually change what the run did, not from ``gate:*`` records alone (a
re-run of a check is not itself a reason a run is done - decision 4).

Whole or nothing (decision 1): every rule runs over every entry, and :class:`Refused` carries
every reason found, never just the first - a planner re-run on one refusal fixes what it can
see, not what stopped the loop that found the first one.

Contents:
    * :class:`Accepted` - the plan, its entries' node ids reallocated by the coordinator.
    * :class:`Refused` - every reason to refuse, in rule order.
    * :func:`validate_plan` - the whole-or-nothing verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ...domain.condition import AllOf, AnyOf, Compare, FieldRef, Not, referenced_fields
from .registry import UnregisteredOp

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
        is_root: Whether ``plan`` is the run's own top-level plan, which must be able to
            tell it is DONE from something other than a ``gate:*`` record alone. ``False``
            for a nested plan a ``plan`` op dispatches, which a parent judges instead.
        allocate_id: Mints one fresh node id per accepted entry; called only on acceptance,
            never while a plan is still being checked.

    Returns:
        :class:`Refused` with every reason found, or :class:`Accepted` with every entry's
        node id - and every reference to it - rewritten to a freshly allocated one.
    """
    reasons: list[str] = [
        *_op_reasons(plan, registry),
        *_args_reasons(plan, registry),
        *_contract_reasons(plan, registry),
        *_dep_reasons(plan, graph),
        *_size_reasons(plan, limits),
    ]
    if is_root:
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


def _contract_reasons(plan: Plan, registry: OpRegistry) -> list[str]:
    """Return one reason per ``FieldRef`` a condition names outside its entry's op contract."""
    entries_by_id = {e.spec.node_id: e for e in plan.entries}
    reasons: list[str] = []
    for cond in _every_condition(plan):
        for ref in referenced_fields(cond):
            reasons.extend(_field_ref_reasons(ref, entries_by_id, registry))
    return reasons


def _field_ref_reasons(ref: FieldRef, entries_by_id: Mapping[str, Entry], registry: OpRegistry) -> list[str]:
    """Return the one reason ``ref`` is not a field its entry's op may ever emit, if any."""
    target = entries_by_id.get(ref.entry)
    if target is None:
        return [f"condition references entry {ref.entry!r}, which this plan does not name"]
    op = _op_or_none(target, registry)
    if op is None:
        return []  # its op is unregistered; _op_reasons already reports that
    if ref.field not in op.output_contract:
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


def _root_state_change_reasons(plan: Plan, registry: OpRegistry) -> list[str]:
    """Return the reason a ROOT plan's ``done_when`` rests only on ops that cannot change state.

    A ``gate:*`` record re-runs a check; it is never itself the reason a run is done (decision
    4). At least one field ``done_when`` reads must belong to an entry whose op CAN change
    state, or a root plan could report itself done from nothing but a gate rerunning cleanly.
    """
    entries_by_id = {e.spec.node_id: e for e in plan.entries}
    for ref in referenced_fields(plan.done_when):
        target = entries_by_id.get(ref.entry)
        if target is None:
            continue
        op = _op_or_none(target, registry)
        if op is not None and op.can_change_state:
            return []
    return [
        "done_when is decided only over entries whose ops cannot change state (gate:*); "
        "a root plan needs at least one that can"
    ]


def _op_or_none(entry: Entry, registry: OpRegistry) -> OpSpec | None:
    """Return ``entry``'s registered op, or ``None`` when it names none - never raises."""
    try:
        return registry.get(entry.op)
    except UnregisteredOp:
        return None


def _reallocated(plan: Plan, allocate_id: Callable[[], str]) -> Plan:
    """Return ``plan`` with every entry's node id freshly allocated, and every reference rewritten."""
    mapping = {e.spec.node_id: allocate_id() for e in plan.entries}
    entries = tuple(_reallocated_entry(e, mapping) for e in plan.entries)
    return plan.model_copy(
        update={
            "entries": entries,
            "holds_while": _remap(plan.holds_while, mapping) if plan.holds_while is not None else None,
            "done_when": _remap(plan.done_when, mapping),
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
