"""RED/GREEN tests for Plan and Entry: round-tripping, the required done_when, and the shipped schema."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from schema_helpers import load, validator

from agentdag.domain.plan import Entry, Plan, evaluate_holds_while, plan_json_schema

SPEC_JSON: dict[str, object] = {
    "node_id": "n0",
    "kind": "work",
    "brief_ref": "nodes/n0/00000000/brief.md",
    "executor": "claude",
    "tier_role": "standard",
    "write_set": [],
    "requires": [],
    "isolation": "none",
    "deps": [],
    "deadline_s": 60,
    "budget": {"tokens": {}},
    "attempt": 0,
}

MINIMAL: dict[str, object] = {
    "goal": "ship",
    "entries": [],
    "deps": [],
    "done_when": {"ref": {"entry": "n0", "field": "status"}, "op": "==", "value": "passed"},
}


def test_plan_round_trips_json_and_requires_done_when() -> None:
    data: dict[str, object] = {
        "goal": "migrate the fleet",
        "deps": [],
        "entries": [
            {
                "spec": SPEC_JSON,
                "op": "work",
                "args": {},
                "brief": "do it",
                "output_contract": ["status"],
                "acceptance": None,
            }
        ],
        "holds_while": None,
        "done_when": {"ref": {"entry": "n0", "field": "status"}, "op": "==", "value": "passed"},
    }
    plan = Plan.model_validate(data)
    assert Plan.model_validate_json(plan.model_dump_json()) == plan
    with pytest.raises(ValidationError):
        Plan.model_validate({**data, "done_when": None})


def test_absent_holds_while_is_vacuously_true() -> None:
    plan = Plan.model_validate(MINIMAL)  # holds_while omitted
    assert plan.holds_while is None
    assert evaluate_holds_while(plan, {}) is True  # the decided absent case, pinned


def test_entry_refuses_free_text() -> None:
    with pytest.raises(ValidationError):
        Entry.model_validate(
            {
                "spec": SPEC_JSON,
                "op": "work",
                "args": {},
                "brief": "do it",
                "output_contract": ["status"],
                "acceptance": None,
                "note": "and the tests pass",
            }
        )


def test_plan_refuses_a_missing_done_when() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate({k: v for k, v in MINIMAL.items() if k != "done_when"})


def test_plan_schema_matches_the_committed_file() -> None:
    """The schema is a shipped artefact: drift between it and the model is a test failure.

    Compares against ``plan_json_schema()`` (not a bare ``Plan.model_json_schema()``):
    the committed file also carries the ``$schema``/``$id`` pair every sibling schema
    has, and both the file and this test are generated from that one function so they
    cannot drift from each other independently of the live model.
    """
    assert load("plan") == plan_json_schema()


def test_plan_with_a_not_condition_validates_against_the_schema_by_default_dump() -> None:
    """Regression for the finding that the default dump used to emit ``not_``.

    A plan whose ``done_when`` is a ``Not`` condition, round-tripped through the
    DEFAULT ``model_dump_json()`` (no ``by_alias`` argument - this is what a real
    persister calls), must still validate against the shipped schema: ``$defs.Not``
    requires the key ``not`` under ``additionalProperties: false`` and rejects
    ``not_`` outright. Before ``serialize_by_alias=True`` was added to ``Not``'s
    config, this failed both assertions.
    """
    data: dict[str, object] = {
        "goal": "gate a push",
        "deps": [],
        "entries": [],
        "holds_while": None,
        "done_when": {
            "not": {"ref": {"entry": "n0", "field": "status"}, "op": "==", "value": "failed"},
        },
    }
    plan = Plan.model_validate(data)
    dumped = plan.model_dump_json()
    assert '"not_"' not in dumped
    assert '"not"' in dumped
    validator("plan").validate(json.loads(dumped))
    assert Plan.model_validate_json(dumped) == plan
