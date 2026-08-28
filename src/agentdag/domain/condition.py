"""A Condition tree over another entry's referenceable view, and its three-valued evaluator.

A Plan's ``acceptance``, ``holds_while`` and ``done_when`` are all one Condition: a
comparison against a named entry's REFERENCEABLE VIEW, or a boolean combination of
smaller conditions. The coordinator branches on these, never on prose a node wrote, so
the shape is a closed, validated tree - no free-text operators, no extra keys - and
deciding it is a pure function with no I/O.

An entry's referenceable view (:func:`referenceable_view`) is its record's ``key_facts``
merged with a small, fixed set of TOP-LEVEL :class:`~agentdag.domain.models.ResultRecord`
fields (:data:`RESERVED_TOP_LEVEL_FIELDS`). ``status`` is in that set and is not a
``key_fact`` at all, so an evaluator that read ``key_facts`` alone could never settle a
condition on the one field every record carries. Exactly one function performs that
merge, so widening the reserved set is a one-place change.

A referenced field that has no record yet (the entry has not run, or its view does not
carry that key) is not a fact this evaluator is willing to guess at: :func:`evaluate`
reports that as ``None`` rather than folding it into ``False``, so a caller can tell
"not yet decidable" from "decided, and it is False" (Kleene's three-valued logic).

Contents:
    * :data:`RESERVED_TOP_LEVEL_FIELDS` - the record fields a condition may name
      besides a ``key_fact``.
    * :func:`referenceable_view` - the ONE merge: a record's key_facts plus those fields.
    * :class:`FieldRef` - one (entry, field) pointer into another entry's view.
    * :class:`Compare` - one leaf: a field against a literal value, under one of six ops.
    * :class:`AllOf`, :class:`AnyOf`, :class:`Not` - the three-valued combinators.
    * :data:`Condition` - the union of all four.
    * :func:`evaluate` - decide a condition tree against a run's records.
    * :func:`referenced_fields` - every :class:`FieldRef` a condition tree reads from.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from .kernel_errors import KernelError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import Any

    from .models import ResultRecord

__all__ = [
    "RESERVED_TOP_LEVEL_FIELDS",
    "AllOf",
    "AnyOf",
    "Compare",
    "Condition",
    "FieldRef",
    "Not",
    "evaluate",
    "referenceable_view",
    "referenced_fields",
]

RESERVED_TOP_LEVEL_FIELDS = frozenset({"status"})
"""The :class:`~agentdag.domain.models.ResultRecord` fields a condition may name alongside
a ``key_fact``.

``status`` alone, deliberately: it is the closed vocabulary the coordinator already
branches on (design 2.2), and it is the field a plan's ``done_when`` most obviously needs.
Every other top-level field stays out until something needs it - widening this set is a
decision of its own, not a default, and this is the one place it would be made."""


def referenceable_view(record: ResultRecord) -> dict[str, object]:
    """Return what a :class:`FieldRef` may read from ``record``: key_facts plus the reserved fields.

    The single place the merge is decided, so :data:`RESERVED_TOP_LEVEL_FIELDS` growing is
    a one-line change and no caller can assemble a different view by hand.

    ``status`` enters as the enum's ``.value`` - a plain :class:`str` - never the
    :class:`~agentdag.domain.models.NodeStatus` member itself: the default ``str()``/
    f-string form of an enum member differs between Python 3.10 and 3.11+, so letting the
    member reach a comparison, a key or a serialised payload makes the answer depend on the
    interpreter. A condition therefore compares against ``NodeStatus.<MEMBER>.value``.

    Args:
        record: The record whose view is wanted.

    Returns:
        ``record.key_facts`` merged with each reserved top-level field.

    Raises:
        KernelError: a ``key_fact`` shadows a reserved name. That is a bug in whatever
            emitted the record, not a precedence puzzle to resolve silently: one of the
            two values would win and every condition naming that field would then read
            something other than what its author meant.

    Example:
        >>> from agentdag.domain.condition import referenceable_view
        >>> from agentdag.domain.models import NodeStatus, ResultRecord
        >>> record = ResultRecord(node_id="n", attempt=0, status=NodeStatus.DONE,
        ...                       key_facts={"rc": 0}, executor_used="code", model_used="-",
        ...                       effort_used="-", duration_s=0.1, input_hash="sha256:0")
        >>> referenceable_view(record) == {"rc": 0, "status": "done"}
        True
    """
    shadowed = sorted(RESERVED_TOP_LEVEL_FIELDS & set(record.key_facts))
    if shadowed:
        raise KernelError(
            f"record {record.node_id!r} has key_facts shadowing reserved top-level field(s) "
            f"{shadowed}; rename the key_fact - a reserved name has exactly one meaning"
        )
    return {**record.key_facts, "status": record.status.value}


class FieldRef(BaseModel):
    """One pointer into another entry's referenceable view: which entry, and which key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: str
    """The referenced entry's node id."""

    field: str
    """A key in that entry's :func:`referenceable_view`."""

    def __hash__(self) -> int:
        """Pydantic already makes a ``frozen=True`` model hashable at runtime by field
        values; pyright's stubs do not encode that dynamic behaviour, so this states the
        same hash explicitly so :func:`referenced_fields` can return a ``frozenset`` of
        these under strict type-checking.
        """
        return hash((self.entry, self.field))


class Compare(BaseModel):
    """One leaf: ``ref``'s value, compared to a literal ``value`` under ``op``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: FieldRef
    op: Literal["==", "!=", "<", "<=", ">", ">="]
    value: int | float | str | bool


class AllOf(BaseModel):
    """True once every child is True; False once any child is False (Kleene AND).

    Named after JSON Schema's own ``allOf`` vocabulary for the same construct. The
    wire field is plain ``all``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    all: tuple[Condition, ...]


class AnyOf(BaseModel):
    """True once any child is True; False once every child is False (Kleene OR).

    Named after JSON Schema's own ``anyOf`` vocabulary for the same construct. The
    wire field is plain ``any``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    any: tuple[Condition, ...]


class Not(BaseModel):
    """Negation of one child condition.

    The Python attribute is ``not_`` because ``not`` is a keyword; it is validated
    and serialised on the wire as ``not``. ``populate_by_name`` also keeps the
    Python name itself usable, so ``Not(not_=...)`` works directly - a plain
    ``alias=`` would make a type checker's synthesised constructor accept only the
    alias, so this uses ``validation_alias``/``serialization_alias`` instead, which
    keeps ``not_`` as the recognised parameter name. ``serialize_by_alias`` makes
    the DEFAULT dump (``model_dump()``/``model_dump_json()`` with no ``by_alias``
    argument) emit ``not`` too - without it, only an explicit ``by_alias=True`` did,
    and the shipped schema's ``additionalProperties: false`` rejects the Python
    name ``not_``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True, serialize_by_alias=True)

    not_: Condition = Field(validation_alias="not", serialization_alias="not")


Condition = Compare | AllOf | AnyOf | Not
"""One condition: a :class:`Compare` leaf, or an :class:`AllOf`/:class:`AnyOf`/:class:`Not`
over child conditions. A plain union, not a discriminated one - the four shapes carry
disjoint field names (``ref``/``op``/``value``, ``all``, ``any``, ``not``), so pydantic
already tells them apart without a tag."""

AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
"""Comparison op name -> the function that applies it. A dict beats an if/elif chain
and makes ``Compare.op``'s closed vocabulary the only place a new op could be added.
Typed loosely (``Any``, not a ``Comparable`` bound): the actual value comes from a
caller's untyped ``key_facts``, so the real type mismatch this cannot catch (comparing
a string to a number with ``<``) is a data problem for the plan author, not one this
module can validate - it surfaces as a ``TypeError`` at the comparison, same as it
would writing the expression by hand."""


def evaluate(cond: Condition, records: Mapping[str, ResultRecord]) -> bool | None:
    """Decide a condition tree against a run's records, three-valued.

    Args:
        cond: The condition tree: a :class:`Compare` leaf, or an :class:`AllOf`/
            :class:`AnyOf`/:class:`Not` over child conditions.
        records: entry id -> that entry's record, exactly as the dispatcher holds them.
            Each one is read through :func:`referenceable_view`, so a condition sees a
            record's ``key_facts`` AND the reserved top-level fields; nothing here can
            assemble a different view.

    Returns:
        ``True`` or ``False`` once the condition is settled. ``None`` when it cannot
        be settled because at least one referenced field is absent - its entry has no
        record in ``records`` yet, or that record's view has no such key.
        Absence is reported to the caller and never silently counted as ``False``.
        The one exception is :class:`AnyOf`, which settles ``True`` the moment any
        child does, even alongside an undecided sibling: a witness that already
        holds cannot be undone by an absence elsewhere. Symmetrically, :class:`AllOf`
        settles ``False`` the moment any child does, for the same reason. Only when
        no child settles the group either way does the absence show through as
        ``None`` (Kleene's three-valued AND/OR).

    Raises:
        KernelError: a referenced record's ``key_facts`` shadows a reserved top-level
            field name (see :func:`referenceable_view`).

    Example:
        >>> from agentdag.domain.condition import Compare, FieldRef, evaluate
        >>> from agentdag.domain.models import NodeStatus, ResultRecord
        >>> gate = ResultRecord(node_id="g", attempt=0, status=NodeStatus.DONE,
        ...                     key_facts={"rc": 0}, executor_used="code", model_used="-",
        ...                     effort_used="-", duration_s=0.1, input_hash="sha256:0")
        >>> cond = Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0)
        >>> evaluate(cond, {"g": gate})
        True
        >>> evaluate(cond, {}) is None
        True
    """
    if isinstance(cond, Compare):
        return _evaluate_compare(cond, records)
    if isinstance(cond, Not):
        child = evaluate(cond.not_, records)
        return None if child is None else not child
    if isinstance(cond, AllOf):
        return _combine([evaluate(child, records) for child in cond.all], dominant=False)
    return _combine([evaluate(child, records) for child in cond.any], dominant=True)


def referenced_fields(cond: Condition) -> frozenset[FieldRef]:
    """Every :class:`FieldRef` a condition tree reads from, gathered recursively.

    Args:
        cond: The condition tree to walk.

    Returns:
        Every :class:`FieldRef` reachable from ``cond``'s leaves. ``FieldRef`` is
        frozen, so two leaves referencing the same (entry, field) collapse to one
        entry in the result.

    Example:
        >>> from agentdag.domain.condition import AllOf, Compare, FieldRef, Not, referenced_fields
        >>> cond = AllOf(all=(Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0),
        ...               Not(not_=Compare(ref=FieldRef(entry="w", field="n"), op="<", value=1))))
        >>> referenced_fields(cond) == frozenset({FieldRef(entry="g", field="rc"),
        ...                                       FieldRef(entry="w", field="n")})
        True
    """
    if isinstance(cond, Compare):
        return frozenset({cond.ref})
    if isinstance(cond, Not):
        return referenced_fields(cond.not_)
    if isinstance(cond, AllOf):
        return frozenset(field for child in cond.all for field in referenced_fields(child))
    return frozenset(field for child in cond.any for field in referenced_fields(child))


def _evaluate_compare(cond: Compare, records: Mapping[str, ResultRecord]) -> bool | None:
    """Decide one ``Compare`` leaf, or report ``None`` when its field is not known yet."""
    record = records.get(cond.ref.entry)
    if record is None:
        return None
    view = referenceable_view(record)
    if cond.ref.field not in view:
        return None
    return bool(_OPS[cond.op](view[cond.ref.field], cond.value))


def _combine(results: Sequence[bool | None], *, dominant: bool) -> bool | None:
    """Kleene AND/OR over a group of child verdicts.

    ``dominant`` is ``False`` for :class:`AllOf` (one ``False`` child settles the whole
    group, whatever its siblings say) and ``True`` for :class:`AnyOf` (one ``True``
    child settles it). An empty group has no child to contradict the dominant-free
    verdict, so it settles to ``not dominant`` - the same vacuous case Python's own
    ``all([])`` (``True``) and ``any([])`` (``False``) already decide, which is what
    the field names ``all``/``any`` are borrowed from.
    """
    if dominant in results:
        return dominant
    if None in results:
        return None
    return not dominant
