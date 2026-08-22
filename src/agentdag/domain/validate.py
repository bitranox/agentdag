"""Whole-spec validation (design 2.4): the refuse rules a planner-emitted spec must pass.

Contents:
    * :func:`validate_spec` - one spec plus the run's limits -> the reasons to refuse it.

A planner-emitted spec is data from an LLM, so the coordinator validates ALL of it before
dispatch rather than trusting the tier alone. This module owns the 2.4 rules that REFUSE and
that slice 1 can express; a refusal is journaled as ``spec_rejected`` with its reasons, the
planner is re-run once carrying them, and a second refusal suspends into ``approve``.

This module owns the rules that need NOTHING but the spec and the run limits. The one 2.4
rule that needs a filesystem - ``brief_ref`` resolving inside the run store after a real
``realpath`` - lives in :mod:`agentdag.application.kernel.dispatchable` behind a port
(decision 10, 2026-08-22), and that module is the entry point that runs both sets.

Rules deliberately absent or partial, none of them an oversight:

* ``write_set`` is enforced only for CONTAINMENT (relative, no traversal above the root, no
  leading glob). 2.4's second half - "inside the node's own dir unless its kind is one the
  run-root exception of 3.1 names" - is NOT implemented, because it does not describe the
  shipping graph. 3.1 names ``manifest/`` for a ``reduce``, ``intents/`` for a ``stage`` and
  ``artefacts/`` for ``reduce``/``synth``, and says every other node writes only under
  ``nodes/<node_id>/<hash8>/``; graph A's ``work`` and ``gate`` nodes declare ``wt/<name>/**``,
  which is neither. Implementing the sentence as written would refuse nodes that run today, so
  the divergence is recorded rather than guessed at.

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

Measured 2026-08-22, and it is why nothing calls this function yet: run over graph A's seven
hand-authored specs, these rules pass six and refuse ``ap_push`` for ``kind: apply``. That
refusal is CORRECT for a planner and WRONG for graph A, whose apply node is hand-authored and
legitimate - 2.4 governs planner-emitted specs only. So this cannot be wired into the dispatch
path; it can only gate a planner's output, and no planner exists. The same run caught a real
defect in the executor rule, which had demanded an ``executor`` that a model kind may legitimately
omit because the resolved ROW supplies it. That control is not a test: asserting it needs graph A's
private spec builders, and reaching into them fails pyright's ``reportPrivateUsage`` with no
precedent in this suite. It belongs in the graph A e2e test once a spec-capturing seam exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .models import CODE_KINDS, FAN_OUT_KINDS, TierRole

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .models import NodeSpec, Requirement
    from .policy import RunLimits

__all__ = ["SpecContext", "SpecVerdict", "validate_spec"]


class SpecContext(BaseModel):
    """What a refuse rule needs BESIDES the spec and the run limits, already resolved.

    Run-scoped and pure: the caller assembles it, so the rules stay free of I/O. A field left
    empty means "the caller has nothing to check against", and the rules that would use it stay
    SILENT rather than guessing - a validator that refuses because the caller passed no graph
    would be worse than one that says nothing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph: dict[str, tuple[str, ...]] = Field(default_factory=dict[str, tuple[str, ...]])
    """Already-admitted node ids to their deps, for existence and acyclicity."""

    resources: dict[str, float] = Field(default_factory=dict[str, float])
    """Registered resource names to their capacity (``PolicyTable.resources``), for ``requires``."""


class SpecVerdict(BaseModel):
    """Why a spec was refused, AND which rules could not be run at all.

    The two are different answers and must not share a shape: empty ``reasons`` with a
    non-empty ``skipped`` means "nothing found, and some rules never ran", which is not the
    same claim as "checked and clean". Reporting only the reasons made those identical, and a
    caller that forgot a context field got a clean verdict that meant nothing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reasons: tuple[str, ...] = ()
    """One human-readable reason per broken rule, in rule order."""

    skipped: tuple[str, ...] = ()
    """Rules that could not run because :class:`SpecContext` carried nothing to check against."""

    @property
    def ok(self) -> bool:
        """Return whether no rule that RAN found anything. See :attr:`complete`."""
        return not self.reasons

    @property
    def complete(self) -> bool:
        """Return whether every rule ran, so :attr:`ok` is a full answer rather than a partial one."""
        return not self.skipped


ROLE_ORDER = (TierRole.MECHANICAL, TierRole.STANDARD, TierRole.DEEP, TierRole.TOP)
"""Ascending role order, stated by design 2.3 rule 2 as ``mechanical < standard < deep < top``.

Lives here because the validator is its only consumer today; 2.3 rule 5's escalation will want
it too, and should move it beside :class:`~agentdag.domain.models.TierRole` when it lands.
"""


def validate_spec(spec: NodeSpec, *, limits: RunLimits, context: SpecContext | None = None) -> SpecVerdict:
    """Return every reason to refuse ``spec``, empty when it may be dispatched.

    Args:
        spec: The planner-emitted spec to validate.
        limits: The run's ceilings and allowlists.
        context: What the rules need besides the spec, already resolved by the caller. Omitted
            means the rules that need it stay silent rather than guessing.

    Returns:
        The reasons to refuse, plus the rules that could not run for want of context. No
        reasons does not mean "clean" unless :attr:`SpecVerdict.complete` is also true, and it
        never means no clamp applies.
    """
    reasons: list[str] = []
    if spec.kind not in limits.planner_kinds:
        allowed = ", ".join(sorted(kind.value for kind in limits.planner_kinds))
        reasons.append(f"kind {spec.kind.value!r} is not planner-emittable; allowed: {allowed}")
    reasons.extend(_executor_reasons(spec))
    reasons.extend(_tier_role_reasons(spec))
    reasons.extend(_ceiling_reasons(spec, limits))
    reasons.extend(_write_set_reasons(spec))
    resolved = context or SpecContext()
    skipped: list[str] = []
    if resolved.graph:
        reasons.extend(_dep_reasons(spec, resolved))
    else:
        reasons.extend(_self_dep_reasons(spec))
        # Only SKIPPED when there was something to check: a rule with no input is vacuously
        # satisfied, and reporting it would make `complete` false for nearly every verdict.
        if spec.deps:
            skipped.append("deps")
    if resolved.resources:
        reasons.extend(_requires_reasons(spec, resolved))
    elif spec.requires:
        skipped.append("requires")
    return SpecVerdict(reasons=tuple(reasons), skipped=tuple(skipped))


def _requires_reasons(spec: NodeSpec, context: SpecContext) -> list[str]:
    """Return the reasons ``spec.requires`` names an unregistered resource or overruns it.

    The bound is INCLUSIVE: a mutex of capacity 1 is taken by asking for exactly 1. The
    caller-registered-nothing case is handled by :func:`validate_spec`, which records it as
    SKIPPED rather than letting the silence read as a pass.
    """
    return [reason for need in spec.requires for reason in _one_requirement_reason(need, context.resources)]


def _one_requirement_reason(need: Requirement, resources: Mapping[str, float]) -> list[str]:
    """Return the single reason ``need`` cannot be admitted against ``resources``, if any."""
    capacity = resources.get(need.resource)
    if capacity is None:
        return [f"requires {need.resource!r}, which is not a registered resource"]
    if need.amount > capacity:
        return [f"requires {need.amount} of {need.resource!r}, over its registered capacity {capacity}"]
    return []


def _self_dep_reasons(spec: NodeSpec) -> list[str]:
    """Return the one-node cycle, which needs no graph and so runs even with no context."""
    if spec.node_id in spec.deps:
        return [f"node {spec.node_id!r} lists itself as a dep, which is a cycle of one"]
    return []


def _dep_reasons(spec: NodeSpec, context: SpecContext) -> list[str]:
    """Return the reasons ``spec.deps`` name a missing node or close a cycle (design 2.4)."""
    self_dep = _self_dep_reasons(spec)
    if self_dep:
        return self_dep
    reasons = [f"dep {dep!r} names no admitted node" for dep in spec.deps if dep not in context.graph]
    if _reaches(spec.node_id, spec.deps, context.graph):
        reasons.append(f"deps of {spec.node_id!r} close a cycle; one of them already depends on it")
    return reasons


def _reaches(target: str, start: Sequence[str], graph: Mapping[str, tuple[str, ...]]) -> bool:
    """Return whether ``target`` is reachable from ``start`` by following dep edges."""
    seen: set[str] = set()
    pending = list(start)
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        pending.extend(graph.get(node, ()))
    return False


def _write_set_reasons(spec: NodeSpec) -> list[str]:
    """Return a reason per ``write_set`` entry that escapes the run root or covers all of it.

    Lexical on purpose: the entries are relative to the isolation root, and no filesystem is
    consulted, so this is pure. It does NOT implement 2.4's second half ("inside the node's own
    dir unless its kind is one the run-root exception of 3.1 names") - see the module docstring.
    """
    return [reason for entry in spec.write_set for reason in _one_write_set_reason(entry)]


def _one_write_set_reason(entry: str) -> list[str]:
    """Return the single reason ``entry`` is not a containable relative path, if it is not."""
    if entry.startswith("/"):
        return [f"write_set {entry!r} is absolute; entries are relative to the run's isolation root"]
    segments = [part for part in entry.split("/") if part not in ("", ".")]
    if not segments:
        return [f"write_set {entry!r} names no path"]
    if any(char in segments[0] for char in "*?["):
        # A leading glob is inside the root and still spans all of it: Coordinator.scan adds
        # every OTHER node's write set to its allow-list, and fnmatch's `*` crosses `/`.
        return [f"write_set {entry!r} starts with a glob, so it covers the whole run root"]
    depth = 0
    for part in segments:
        depth += -1 if part == ".." else 1
        if depth < 0:
            return [f"write_set {entry!r} traverses above the run's isolation root"]
    return []


def _ceiling_reasons(spec: NodeSpec, limits: RunLimits) -> list[str]:
    """Return the reason ``spec.tier_role`` outranks its kind's ``per_kind_ceiling``.

    REFUSES rather than clamping (DECISIONS.md item 9), which amends design 2.3 rule 4: a
    clamp is silent this milestone, so a judge quietly dropped from ``top`` to ``deep`` would
    still return a verdict the coordinator branches on.

    An ABSENT ceiling entry fails CLOSED (user, 2026-08-22): unconfigured is not uncapped.
    Failing open would make the cap config-shaped rather than code-shaped, so deleting one line
    from ``per_kind_ceiling`` would silently remove it for that kind with no error anywhere.
    """
    if spec.tier_role is None:
        return []
    ceiling = limits.per_kind_ceiling.get(spec.kind.value)
    if ceiling is None:
        return [f"kind {spec.kind.value!r} declares no per_kind_ceiling, so it may not carry a tier_role"]
    if ROLE_ORDER.index(spec.tier_role) <= ROLE_ORDER.index(ceiling):
        return []
    return [f"tier_role {spec.tier_role.value!r} outranks the {spec.kind.value!r} ceiling {ceiling.value!r}"]


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
    elif named == "code":
        # None is legitimate: LoadedPolicy.resolve takes the executor from the resolved model
        # ROW, so a model kind carrying tier_role and model alone (graph A's w_migrate) is valid.
        return [f"kind {spec.kind.value!r} is model-executed, so its executor may not be 'code'"]
    return []
