"""RED/GREEN tests for Plan and Entry: round-tripping, the required done_when, and the shipped schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from schema_helpers import load

from agentdag.domain.plan import Entry, Plan, evaluate_holds_while

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
    """The schema is a shipped artefact: drift between it and the model is a test failure."""
    assert load("plan") == Plan.model_json_schema()
