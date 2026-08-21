"""Whole-spec validation (design 2.4): the refuse rules a planner-emitted spec must pass.

Contents:
    * :func:`validate_spec` - one spec plus the run's limits -> the reasons to refuse it.

A planner-emitted spec is data from an LLM, so the coordinator validates ALL of it before
dispatch rather than trusting the tier alone. This module owns the 2.4 rules that REFUSE and
that slice 1 can express; a refusal is journaled as ``spec_rejected`` with its reasons, the
planner is re-run once carrying them, and a second refusal suspends into ``approve``.

Three rules are deliberately absent, and none of them is an oversight:

* ``knowledge`` and ``stage_into`` against the workflow's grant - design 2.1 puts both outside
  slice 1 ("NOT IN SLICE 1: both need semdex 4.4 and 4.9, neither shipped").
* ``budget`` against ``tokens_per_row`` - already enforced, and as a REFUSAL, by
  :meth:`~agentdag.application.kernel.context.Coordinator._run_cap_refusal`, which returns a
  non-transient ``BUDGET_EXCEEDED`` record without calling the executor. Duplicating it here
  would give one rule two homes that can disagree.
* ``tier_role`` against ``per_kind_ceiling`` - the one genuine open collision, since design 2.3
  rule 4 CLAMPS it and 2.4 REFUSES it. It is not reachable until a planner exists to over-ask,
  and ``2026-08-22-clamp-or-refuse.md`` records why the obvious partition does not settle it.

Known inconsistency, recorded rather than resolved: 2.4 lists ``deadline_s`` over
``deadline_ceiling_s`` as a refuse rule, while ``context.py:246`` silently clamps it with
``min()``. The shipped clamp is tested and is left alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import Kind

if TYPE_CHECKING:
    from .models import NodeSpec
    from .policy import RunLimits

__all__ = ["validate_spec"]

CODE_KINDS = frozenset({Kind.GATE, Kind.REDUCE, Kind.WAIT, Kind.STAGE, Kind.APPLY, Kind.APPROVE})
"""The kinds the coordinator runs as code, which carry ``executor: "code"`` (design 2.1)."""

FAN_OUT_KINDS = frozenset({Kind.MAP, Kind.BATCH})
"""Fan-out and fold performed by the coordinator itself, which carry NO executor (design 2.1)."""


def validate_spec(spec: NodeSpec, *, limits: RunLimits) -> tuple[str, ...]:
    """Return every reason to refuse ``spec``, empty when it may be dispatched.

    Args:
        spec: The planner-emitted spec to validate.
        limits: The run's ceilings and allowlists.

    Returns:
        One human-readable reason per broken rule, in rule order. Empty means the spec
        passes every refuse rule; it does not mean no clamp applies.
    """
    reasons: list[str] = []
    if spec.kind not in limits.planner_kinds:
        allowed = ", ".join(sorted(kind.value for kind in limits.planner_kinds))
        reasons.append(f"kind {spec.kind.value!r} is not planner-emittable; allowed: {allowed}")
    reasons.extend(_executor_reasons(spec))
    reasons.extend(_tier_role_reasons(spec))
    return tuple(reasons)


def _tier_role_reasons(spec: NodeSpec) -> list[str]:
    """Return the reason ``spec.tier_role`` is set on a kind that resolves no model row.

    Design 2.1 and 2.3 rule 1 both state this the same way, so it is a refuse rule rather
    than a case of the ``per_kind_ceiling`` question ``2026-08-22-clamp-or-refuse.md`` asks.
    """
    if spec.tier_role is None or spec.kind not in (CODE_KINDS | FAN_OUT_KINDS):
        return []
    return [f"kind {spec.kind.value!r} resolves no model row, so tier_role must be null, not {spec.tier_role.value!r}"]


def _executor_reasons(spec: NodeSpec) -> list[str]:
    """Return the reason ``spec.executor`` disagrees with its kind, if it does (design 2.1)."""
    named = spec.executor
    if spec.kind in CODE_KINDS:
        if named != "code":
            return [f"kind {spec.kind.value!r} is code-executed, so its executor must be 'code', not {named!r}"]
    elif spec.kind in FAN_OUT_KINDS:
        if named is not None:
            return [f"kind {spec.kind.value!r} is coordinator fan-out and carries no executor, but named {named!r}"]
    elif named is None or named == "code":
        return [f"kind {spec.kind.value!r} is model-executed, so its executor must name a model runner, not {named!r}"]
    return []
