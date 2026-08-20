"""The tier policy table: model rows, kind defaults, thresholds and run limits (design 2.3).

Loaded from a YAML file (see :mod:`agentdag.adapters.kernel.policy_yaml`) into these pydantic
models, every one ``extra="forbid"`` - an operator's typo in the table is a load error, not a
silently ignored key. :func:`resolve_row` is the ONE place a tier role (and any explicit model
override) turns into a model row; it is pure, so it is exercised directly against a table built
in memory, not only through the shipped YAML.

Only the one-shot resolution rule of design 2.3 (rule 3) lives here. The one-role-down
fallback (rule 2), the run-limit clamps (rule 4) and escalation (rule 5) are planner-driven and
are not implemented by this module (out of scope, M2).

Contents:
    * :class:`TierRow` - one model row: alias, executor, cost, availability, the roles it serves.
    * :class:`KindDefault` - the role (and effort) a kind resolves to when a spec names none.
    * :class:`Escalation` - the retry-one-rank-up rule design 2.3 rule 5 applies over this table.
    * :class:`Thresholds` - the three named thresholds of design 3.5, as data.
    * :class:`RunLimits` - the whole-run ceilings design 2.3 ("Run limits")/2.4/3.4 validate against.
    * :class:`ResourceRow` - one shared resource: a mutex, a capacity, or a rate (design 3.6).
    * :class:`PolicyTable` - the whole table, as loaded from YAML.
    * :func:`resolve_row` - resolve a tier role and optional model override to one row.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .kernel_errors import SpecRejected
from .models import Kind, TierRole

__all__ = [
    "Escalation",
    "KindDefault",
    "PolicyTable",
    "ResourceRow",
    "RunLimits",
    "Thresholds",
    "TierRow",
    "resolve_row",
]


class TierRow(BaseModel):
    """One model row of the tier policy table (design 2.3): a model's cost, availability, roles.

    ``rank`` is the operator's own ordering, used only to pick "the cheapest available row
    listing a role" (here) and "the next higher rank listing the role" on escalation (2.3 rule
    2) - it carries no meaning beyond relative order within one table. ``usd_per_mtoken`` keys
    the design's three billing dimensions (``in``, ``out``, ``cache_read``) to a USD-per-million-
    token price, or ``None`` where the provider's price is not yet known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    alias: str
    executor: str
    rank: int
    cost_class: str
    available: bool
    roles: list[TierRole]
    allowed_efforts: list[str]
    handover_at_tokens: int
    usd_per_mtoken: dict[str, float | None]
    billing: str


class KindDefault(BaseModel):
    """The role (and effort) a kind resolves to when a spec names none (design 2.3 rule 1).

    ``tier_role`` is ``None`` for a kind whose executor is code (gate, reduce, wait, stage,
    apply, approve) or that resolves no model row of its own (map, batch, whose default belongs
    to their body nodes) - such a kind never resolves a model row. ``effort`` of ``"inherit"``
    is the design's own sentinel for "take the value from the calling context"; it is never one
    of a row's ``allowed_efforts``, and never a node value (an inheriting node omits ``effort``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier_role: TierRole | None
    effort: str | None


class Escalation(BaseModel):
    """The retry-one-rank-up rule design 2.3 rule 5 applies over this table (out of scope, M2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_hops: int
    then: str
    no_higher_row: str
    on_auth_failure: str


class Thresholds(BaseModel):
    """The three named thresholds of design 3.5, as policy-table data, not code constants.

    ``min_node_minutes`` is a FRACTION of a minute, hence ``float``: it is the per-dispatch
    startup cost divided by the acceptable overhead fraction and by throughput, and those
    measured inputs put it well under one minute, so a whole-minute type cannot express it.
    The other three count whole things (nodes, lines, handovers) and stay ``int``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_node_minutes: float
    reduce_tree_fanin: int
    journal_max_lines: int
    max_continuations: int


class RunLimits(BaseModel):
    """The whole-run ceilings design 2.3 ("Run limits")/2.4/3.4 validate and clamp against."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tokens_per_row: dict[str, int]
    deadline_ceiling_s: float
    per_kind_ceiling: dict[str, TierRole]
    planner_kinds: list[Kind]
    top_role_budget_floor: float


class ResourceRow(BaseModel):
    """One shared resource this table names (design 3.6): a mutex, a capacity, or a rate.

    In M2 this is loaded into models and otherwise unused - the host-level lease registry, the
    probes and the discovery pass that consume these rows are later work (out of scope here).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: str
    unit: str
    capacity: float
    window: str | None = None
    probe: list[str] | None = None
    probe_cadence_s: float | None = None
    probe_ttl_s: float | None = None
    enforce: list[str] | None = None


class PolicyTable(BaseModel):
    """The whole tier policy table, as loaded from YAML (design 2.3): rows, defaults, limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    models: list[TierRow]
    kind_defaults: dict[str, KindDefault]
    escalation: Escalation
    thresholds: Thresholds
    run_limits: RunLimits
    resources: list[ResourceRow]


def resolve_row(table: PolicyTable, *, tier_role: TierRole | None, model: str | None) -> TierRow:
    """Resolve a tier role and optional model override to one row of ``table`` (design 2.3 rule 3).

    Args:
        table: The policy table to resolve against.
        tier_role: The role to resolve, or ``None`` when the caller has none to offer - a code
            kind with no kind-default role, or a spec naming neither a role nor a model.
        model: An explicit model alias override, or ``None`` to resolve by role alone.

    Returns:
        The row ``model`` names (when given), otherwise the cheapest available row of
        ``tier_role`` by ascending ``rank``.

    Raises:
        SpecRejected: ``model`` names no row, an unavailable row, or a row that does not list
            ``tier_role`` (when one is given); or no ``model`` is given and either ``tier_role``
            is ``None`` or no available row lists it.

    Example:
        >>> from agentdag.domain.models import TierRole
        >>> data = {
        ...     "models": [{"alias": "a", "executor": "claude", "rank": 1, "cost_class": "low",
        ...                 "available": True, "roles": ["standard"], "allowed_efforts": ["low"],
        ...                 "handover_at_tokens": 1, "usd_per_mtoken": {"in": 1.0}, "billing": "metered"}],
        ...     "kind_defaults": {},
        ...     "escalation": {"max_hops": 1, "then": "approve", "no_higher_row": "approve",
        ...                    "on_auth_failure": "fail_run"},
        ...     "thresholds": {"min_node_minutes": 1, "reduce_tree_fanin": 1, "journal_max_lines": 1,
        ...                    "max_continuations": 1},
        ...     "run_limits": {"tokens_per_row": {}, "deadline_ceiling_s": 1.0, "per_kind_ceiling": {},
        ...                    "planner_kinds": [], "top_role_budget_floor": 0.0},
        ...     "resources": [],
        ... }
        >>> table = PolicyTable.model_validate(data)
        >>> resolve_row(table, tier_role=TierRole.STANDARD, model=None).alias
        'a'
    """
    if model is not None:
        return _resolve_by_model(table, tier_role=tier_role, model=model)
    if tier_role is None:
        raise SpecRejected("no tier_role and no model to resolve a policy row from")
    candidates = sorted(
        (row for row in table.models if row.available and tier_role in row.roles),
        key=lambda row: row.rank,
    )
    if not candidates:
        raise SpecRejected(f"no available row lists role {tier_role.value!r}")
    return candidates[0]


def _resolve_by_model(table: PolicyTable, *, tier_role: TierRole | None, model: str) -> TierRow:
    """Resolve an explicit model override: it must exist, be available, and list ``tier_role``.

    Raises:
        SpecRejected: see :func:`resolve_row`.
    """
    row = next((row for row in table.models if row.alias == model), None)
    if row is None:
        raise SpecRejected(f"model {model!r} names no policy row")
    if not row.available:
        raise SpecRejected(f"model {model!r} is not available")
    if tier_role is not None and tier_role not in row.roles:
        raise SpecRejected(f"model {model!r} does not list role {tier_role.value!r}")
    return row
