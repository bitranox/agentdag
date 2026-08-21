"""Whole-spec validation (design 2.4): the refuse rules a planner-emitted spec must pass.

Contents:
    * :func:`validate_spec` - one spec plus the run's limits -> the reasons to refuse it.

A planner-emitted spec is data from an LLM, so the coordinator validates ALL of it before
dispatch rather than trusting the tier alone. This module owns only the rules that REFUSE.
The ceilings design 2.3 rule 4 CLAMPS against (``tier_role``, ``deadline_s``, ``budget``) are
not here: a ceiling on the run's own resources is an over-ask the coordinator answers by
handing out less, while a boundary the workflow author set is answered by refusing.
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
    return tuple(reasons)


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
