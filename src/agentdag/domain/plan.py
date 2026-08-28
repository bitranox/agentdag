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
    * :data:`PLAN_SCHEMA_ID`, :func:`plan_json_schema` - the ``$id`` this schema
      ships under, and the single source both the committed
      ``schemas/plan.schema.json`` and its drift test are generated from.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .condition import Condition, evaluate
from .models import NodeSpec

if TYPE_CHECKING:
    from .models import ResultRecord

__all__ = ["PLAN_SCHEMA_ID", "Entry", "Plan", "evaluate_holds_while", "plan_json_schema"]


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


def evaluate_holds_while(plan: Plan, records: Mapping[str, ResultRecord]) -> bool | None:
    """Decide ``plan.holds_while``, treating an absent guard as vacuously true.

    Args:
        plan: The plan whose guard is being checked.
        records: entry id -> that entry's record, as
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
        ...     "done_when": {"ref": {"entry": "n0", "field": "status"}, "op": "==", "value": "done"},
        ... })
        >>> evaluate_holds_while(plan, {})
        True
    """
    if plan.holds_while is None:
        return True
    return evaluate(plan.holds_while, records)


PLAN_SCHEMA_ID = "https://agentdag.internal/schemas/plan.schema.json"
"""This schema's ``$id``, matching the URI shape every other shipped schema in
``agentdag/schemas/`` uses (e.g. ``node-spec.schema.json``'s own ``$id``)."""


def plan_json_schema() -> dict[str, object]:
    """``Plan.model_json_schema()``, augmented with the ``$schema``/``$id`` pair every
    other shipped schema in ``agentdag/schemas/`` carries.

    ``model_json_schema()`` alone omits both: pydantic has no opinion on where a
    schema is hosted. The committed ``schemas/plan.schema.json`` and the drift test
    in ``tests/test_domain_plan.py`` both call this one function rather than each
    hardcoding the same two literals, so they cannot drift from EACH OTHER - only
    from the live ``Plan`` model, which is exactly what the drift test exists to
    catch.

    Returns:
        The full schema dict, with ``$schema`` and ``$id`` first (matching the
        sibling schemas' own key order), followed by whatever
        ``Plan.model_json_schema()`` produces.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": PLAN_SCHEMA_ID,
        **Plan.model_json_schema(),
    }
