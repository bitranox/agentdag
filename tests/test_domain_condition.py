"""RED/GREEN tests for the domain condition tree and its three-valued evaluator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentdag.domain.condition import AllOf, AnyOf, Compare, FieldRef, Not, evaluate, referenced_fields


def test_compare_evaluates_against_key_facts() -> None:
    cond = Compare(ref=FieldRef(entry="w_scan", field="repo_count"), op="<=", value=20)
    assert evaluate(cond, {"w_scan": {"repo_count": 47}}) is False
    assert evaluate(cond, {"w_scan": {"repo_count": 12}}) is True


def test_absent_field_is_none_not_false() -> None:
    cond = Compare(ref=FieldRef(entry="w_scan", field="repo_count"), op="<=", value=20)
    assert evaluate(cond, {}) is None  # entry missing
    assert evaluate(cond, {"w_scan": {}}) is None  # field missing


def test_all_any_not_compose_and_none_propagates() -> None:
    a = Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0)
    b = Compare(ref=FieldRef(entry="w", field="count"), op=">", value=0)
    recs = {"g": {"rc": 0}}  # b's field absent
    assert evaluate(AllOf(all=(a, b)), recs) is None  # cannot say True
    assert evaluate(AnyOf(any=(a, b)), recs) is True  # a alone decides
    assert evaluate(Not(not_=a), recs) is False


def test_all_false_dominates_even_alongside_an_absent_sibling() -> None:
    """The direction opposite the brief's own example: a decided False also outranks None."""
    a = Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=1)  # False: rc is 0
    b = Compare(ref=FieldRef(entry="w", field="count"), op=">", value=0)  # absent
    recs = {"g": {"rc": 0}}
    assert evaluate(AllOf(all=(a, b)), recs) is False


def test_not_of_none_is_none() -> None:
    cond = Compare(ref=FieldRef(entry="w", field="n"), op=">", value=0)
    assert evaluate(Not(not_=cond), {}) is None


def test_empty_all_is_vacuously_true_and_empty_any_is_vacuously_false() -> None:
    """Decided deliberately: mirrors Python's own all([]) is True, any([]) is False."""
    assert evaluate(AllOf(all=()), {}) is True
    assert evaluate(AnyOf(any=()), {}) is False


def test_referenced_fields_lists_every_ref() -> None:
    """Nests an AnyOf with two DISTINCT children so a fold that stops at the first child
    (e.g. ``return referenced_fields(cond.any[0])``) drops a real ref and fails this test -
    the earlier version of this test only exercised AnyOf via a dedup case where both
    children share one ref, which such a mutation cannot be told apart from.
    """
    cond = AllOf(
        all=(
            Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0),
            Not(not_=Compare(ref=FieldRef(entry="w", field="n"), op="<", value=1)),
            AnyOf(
                any=(
                    Compare(ref=FieldRef(entry="a", field="x"), op="==", value=1),
                    Compare(ref=FieldRef(entry="b", field="y"), op="==", value=2),
                )
            ),
        )
    )
    assert referenced_fields(cond) == frozenset(
        {
            FieldRef(entry="g", field="rc"),
            FieldRef(entry="w", field="n"),
            FieldRef(entry="a", field="x"),
            FieldRef(entry="b", field="y"),
        }
    )


def test_referenced_fields_dedups_a_repeated_ref() -> None:
    ref = FieldRef(entry="g", field="rc")
    cond = AnyOf(any=(Compare(ref=ref, op="==", value=0), Compare(ref=ref, op="!=", value=1)))
    assert referenced_fields(cond) == frozenset({ref})


def test_condition_refuses_free_text_and_unknown_op() -> None:
    with pytest.raises(ValidationError):
        Compare.model_validate({"ref": {"entry": "g", "field": "rc"}, "op": "matches", "value": "x"})
    with pytest.raises(ValidationError):
        Compare.model_validate(
            {"ref": {"entry": "g", "field": "rc"}, "op": "==", "value": 0, "note": "and the tests pass"}
        )


def test_not_round_trips_via_its_wire_alias() -> None:
    cond = Not(not_=Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0))
    dumped = cond.model_dump(by_alias=True)
    assert dumped["not"]["ref"] == {"entry": "g", "field": "rc"}
    assert Not.model_validate(dumped) == cond


def test_not_default_dump_also_uses_the_wire_alias() -> None:
    """serialize_by_alias=True on Not: the DEFAULT dump (no by_alias=True) must already
    emit "not", because that is the only shape the shipped schema accepts."""
    cond = Not(not_=Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0))
    dumped = cond.model_dump()
    assert "not" in dumped
    assert "not_" not in dumped
    assert Not.model_validate(dumped) == cond
