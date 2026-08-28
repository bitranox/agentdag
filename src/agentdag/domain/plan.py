"""Plan and Entry: what a planner emits, gated by Conditions the coordinator can check.

A workflow author or a planner node produces a :class:`Plan`: an ordered list of
:class:`Entry` steps, each one a :class:`~agentdag.domain.models.NodeSpec` plus the op
that will run it, and a :class:`~agentdag.domain.condition.Condition` the whole plan
must keep holding while it runs (``holds_while``) and one it must reach to be done
(``done_when``). Both are decided by
:func:`~agentdag.domain.condition.evaluate` against the run's own records, never by an
agent's prose - that is the whole point of typing them as :class:`~agentdag.domain.
condition.Condition` trees rather than free text. This module owns only the SHAPE: it
knows nothing about which ops exist (the registry, a later task) or how a plan gets
validated against one.

Contents:
    * :class:`Entry` - one dispatchable step: a spec, an op, its args, brief and
      output contract, and an optional per-entry acceptance condition.
    * :class:`Plan` - a goal, its entries, and the two run-level conditions.
    * :func:`evaluate_holds_while` - decide a plan's ``holds_while``, an absent
      guard reading as vacuously true.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from .condition import Condition, evaluate
from .models import NodeSpec

__all__ = ["Entry", "Plan", "evaluate_holds_while"]


class Entry(BaseModel):
    """One dispatchable step of a :class:`Plan`.

    ``op`` names a registry entry (or the literal ``"plan"`` for a nested planner
    step) - the registry itself is a later task, so this model only carries the name,
    never resolves it. ``acceptance`` is this entry's own gate, separate from the
    plan-wide ``done_when``: a plan can require every entry to individually pass
    while also stating what the plan as a whole must reach.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: NodeSpec
    """What the coordinator dispatches for this entry."""

    op: str
    """A registry name, or ``"plan"`` for a nested planner step."""

    args: Mapping[str, object]
    """The op's own arguments, opaque to this model."""

    brief: str
    """The brief text handed to the dispatched node."""

    output_contract: frozenset[str]
    """The ``key_facts`` names this entry promises to produce."""

    acceptance: Condition | None = None
    """This entry's own pass/fail gate, or ``None`` when it has none of its own."""


class Plan(BaseModel):
    """What a planner produces for one goal: its entries and the conditions gating them.

    ``done_when`` is REQUIRED - a plan with no way to tell it is finished is not a
    plan the coordinator can run unattended. ``holds_while`` is optional: an absent
    guard is vacuously true (see :func:`evaluate_holds_while`), since most plans have
    nothing they need to keep holding beyond their entries' own acceptance gates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    """A one-line statement of what this plan is for."""

    entries: tuple[Entry, ...]
    """The steps to dispatch, in the order the planner emitted them."""

    holds_while: Condition | None = None
    """A guard the run must keep satisfying while the plan is in flight; absent = true."""

    done_when: Condition
    """The condition that says the plan has reached its goal. Required."""

    deps: tuple[str, ...] = ()
    """entry ids (or outer node ids) this plan as a whole depends on."""


def evaluate_holds_while(plan: Plan, records: Mapping[str, Mapping[str, object]]) -> bool | None:
    """Decide ``plan.holds_while``, treating an absent guard as vacuously true.

    Args:
        plan: The plan whose guard is being checked.
        records: entry id -> that entry's ``key_facts``, as
            :func:`~agentdag.domain.condition.evaluate` takes.

    Returns:
        ``True`` when ``plan.holds_while`` is ``None`` - a plan that names no guard
        constrains nothing while it runs, so there is nothing for it to violate.
        Otherwise the guard's own three-valued verdict from
        :func:`~agentdag.domain.condition.evaluate`.

    Example:
        >>> from agentdag.domain.plan import Plan, evaluate_holds_while
        >>> plan = Plan.model_validate({
        ...     "goal": "ship", "entries": [], "deps": [],
        ...     "done_when": {"ref": {"entry": "n0", "field": "status"}, "op": "==", "value": "passed"},
        ... })
        >>> evaluate_holds_while(plan, {})
        True
    """
    if plan.holds_while is None:
        return True
    return evaluate(plan.holds_while, records)
