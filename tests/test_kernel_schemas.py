"""Schema conformance: the seam between the design's JSON schemas and the kernel domain models."""

from __future__ import annotations

import json

import pytest
from schema_helpers import load, validator

from agentdag.domain.journal import dump_journal_line, parse_journal_line
from agentdag.domain.models import ApprovePayload, NodeSpec, ResultRecord


@pytest.mark.parametrize("name", ["node-spec", "result-record", "journal-line", "approve-payload"])
def test_every_schema_example_validates_against_its_schema(name: str) -> None:
    v = validator(name)
    for example in load(name)["examples"]:
        v.validate(example)


def test_models_accept_the_schema_examples_and_emit_valid_json() -> None:
    for ex in load("node-spec")["examples"]:
        NodeSpec.model_validate({k: v for k, v in ex.items() if k != "compact"})  # compact is not a slice-1 field
    for ex in load("result-record")["examples"]:
        validator("result-record").validate(json.loads(ResultRecord.model_validate(ex).model_dump_json(by_alias=True)))
    for ex in load("approve-payload")["examples"]:
        validator("approve-payload").validate(json.loads(ApprovePayload.model_validate(ex).model_dump_json()))
    for ex in load("journal-line")["examples"]:
        modeled_events = {
            "started",
            "result",
            "run_started",
            "resume",
            "approve_decision",
            "run_summary",
            "cancel_requested",
            "cancel",
        }
        if ex["event"] in modeled_events:
            validator("journal-line").validate(json.loads(dump_journal_line(parse_journal_line(json.dumps(ex)))))


def test_a_pre_sandbox_record_with_no_sandbox_key_validates_and_round_trips() -> None:
    """An M2-era record (dispatched before Task 19 added ``sandbox``) still validates and round-trips.

    M2's own journals carry no ``sandbox`` key at all - not a ``null`` value, the key is
    simply absent, because the field did not exist yet. ``ResultRecord`` must accept that
    shape (``sandbox`` defaults to ``None``) and its dump must OMIT the key again rather than
    writing it out as ``null``, which the schema's ``sandbox`` property (typed ``"object"``
    only, no ``null`` in its ``type`` list) would reject. The shipped schema examples cover
    this shape incidentally (two of the three omit ``sandbox``), but only the "present"
    direction has a test that names what it is proving; this one is that test for the
    "absent" direction.
    """
    m2_record = {
        "node_id": "w_pre_sandbox",
        "attempt": 0,
        "status": "done",
        "artefact_refs": ["artefacts/w_pre_sandbox/out.md"],
        "key_facts": {},
        "typed_fields": [],
        "charged_tokens": {"sonnet": 100},
        "cost_usd": 0.001,
        "tokens": {"in": 100, "out": 10, "cache_read": 0, "reasoning": 0},
        "duration_s": 1.0,
        "executor_used": "claude",
        "model_used": "sonnet",
        "effort_used": "medium",
        "knowledge_used": [],
        "input_hash": "v2:sha256:" + "0" * 64,
    }
    assert "sandbox" not in m2_record

    record = ResultRecord.model_validate(m2_record)
    assert record.sandbox is None

    dumped = json.loads(record.model_dump_json(by_alias=True))
    assert "sandbox" not in dumped
    validator("result-record").validate(dumped)
