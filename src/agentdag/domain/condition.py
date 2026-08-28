"""A Condition tree over another entry's key_facts, and its pure, three-valued evaluator.

A Plan's ``acceptance``, ``holds_while`` and ``done_when`` are all one Condition: a
comparison against a named entry's ``key_facts`` (:attr:`~agentdag.domain.journal.
ResultLine.record`'s own field), or a boolean combination of smaller conditions. The
coordinator branches on these, never on prose a node wrote, so the shape is a closed,
validated tree - no free-text operators, no extra keys - and deciding it is a pure
function with no I/O.

A referenced field that has no record yet (the entry has not run, or its record does
not carry that key) is not a fact this evaluator is willing to guess at: :func:`evaluate`
reports that as ``None`` rather than folding it into ``False``, so a caller can tell
"not yet decidable" from "decided, and it is False" (Kleene's three-valued logic).

Contents:
    * :class:`FieldRef` - one (entry, field) pointer into another entry's ``key_facts``.
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

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import Any

__all__ = [
    "AllOf",
    "AnyOf",
    "Compare",
    "Condition",
    "FieldRef",
    "Not",
    "evaluate",
    "referenced_fields",
]


class FieldRef(BaseModel):
    """One pointer into another entry's ``key_facts``: which entry, and which key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: str
    """The referenced entry's node id."""

    field: str
    """A key in that entry's ``key_facts`` dict."""

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


def evaluate(cond: Condition, records: Mapping[str, Mapping[str, object]]) -> bool | None:
    """Decide a condition tree against a run's records, three-valued.

    Args:
        cond: The condition tree: a :class:`Compare` leaf, or an :class:`AllOf`/
            :class:`AnyOf`/:class:`Not` over child conditions.
        records: entry id -> that entry's ``key_facts`` (mirrors
            :attr:`~agentdag.domain.journal.ResultLine.record`'s ``key_facts`` field,
            taken as a plain mapping so this module stays pure and needs no journal
            type).

    Returns:
        ``True`` or ``False`` once the condition is settled. ``None`` when it cannot
        be settled because at least one referenced field is absent - its entry has no
        record in ``records`` yet, or that record's ``key_facts`` has no such key.
        Absence is reported to the caller and never silently counted as ``False``.
        The one exception is :class:`AnyOf`, which settles ``True`` the moment any
        child does, even alongside an undecided sibling: a witness that already
        holds cannot be undone by an absence elsewhere. Symmetrically, :class:`AllOf`
        settles ``False`` the moment any child does, for the same reason. Only when
        no child settles the group either way does the absence show through as
        ``None`` (Kleene's three-valued AND/OR).

    Example:
        >>> from agentdag.domain.condition import Compare, FieldRef, evaluate
        >>> cond = Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0)
        >>> evaluate(cond, {"g": {"rc": 0}})
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


def _evaluate_compare(cond: Compare, records: Mapping[str, Mapping[str, object]]) -> bool | None:
    """Decide one ``Compare`` leaf, or report ``None`` when its field is not known yet."""
    facts = records.get(cond.ref.entry)
    if facts is None or cond.ref.field not in facts:
        return None
    return bool(_OPS[cond.op](facts[cond.ref.field], cond.value))


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
