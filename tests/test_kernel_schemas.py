"""Schema conformance: the seam between the design's JSON schemas and the kernel domain models."""

from __future__ import annotations

import json
from typing import get_args

import pytest
from schema_helpers import load, validator

from agentdag.domain.journal import (
    JournalLine,
    PlanAcceptedLine,
    PlanInvalidatedLine,
    SubtreeDoneLine,
    dump_journal_line,
    parse_journal_line,
)
from agentdag.domain.models import ApprovePayload, ErrorType, NodeSpec, ResultRecord


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


@pytest.mark.os_agnostic
def test_the_result_record_schemas_error_enum_is_exactly_the_domain_error_vocabulary() -> None:
    """The schema's ``error.type`` enum and :class:`ErrorType` must not drift apart.

    Both claim to BE "the closed error vocabulary of design 2.2", and a record is written
    from the Python enum but validated against the JSON one, so a member in only one of
    them is a record the producer can emit and the validator must reject. Asserting set
    equality rather than containment catches the drift in BOTH directions: a member added
    to the enum and not the schema (which is how ``continuation_limit`` went missing), and
    a stale schema member no producer can emit any more.
    """
    schema = load("result-record")
    assert set(schema["properties"]["error"]["properties"]["type"]["enum"]) == {member.value for member in ErrorType}


def test_the_three_replan_lines_default_dump_validates_against_the_schema() -> None:
    """Task 35 step 5. Dumped with NO arguments, which is the path production takes.

    The neighbouring round-trip test proves each line parses back to its own type, which a
    line whose wire shape the SCHEMA rejects would also pass. This is the other half, and it
    is the half this repo has already been caught by: a field whose default dump the schema
    refused, while one test passed ``by_alias=True`` and the other never reached the branch.
    """
    v = validator("journal-line")
    key = "v2:sha256:" + "0" * 64
    at = "2026-08-17T09:12:03+00:00"
    lines = (
        PlanAcceptedLine(key=key, node_id="p", entries=3, at=at),
        PlanInvalidatedLine(key=key, node_id="p", reasons=("g.rc == 1",), at=at),
        SubtreeDoneLine(key=key, node_id="p", done=True, at=at),
    )

    for line in lines:
        v.validate(json.loads(line.model_dump_json()))


def test_the_journal_line_schema_covers_every_line_type_the_union_admits() -> None:
    """A model added to the union without a schema def would only fail on a REAL run.

    ``test_kernel_run.py`` validates a real run's journal against this schema, so the gap
    surfaces there - but only once something actually emits the new line, which can be many
    commits after the model lands. This closes it at the type level instead.
    """
    # get_args(JournalLine) is (the union, the discriminator FieldInfo); get_args of that
    # union is the member models. Each member's `event` is a Literal with that string as its
    # default, which is the value the discriminator and the schema's `const` both key on.
    events = {model.model_fields["event"].default for model in get_args(get_args(JournalLine)[0])}
    consts = {d["properties"]["event"]["const"] for d in load("journal-line")["$defs"].values() if "properties" in d}

    assert events <= consts, f"union events with no schema def: {sorted(events - consts)}"
