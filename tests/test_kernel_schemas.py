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
        if ex["event"] in {"started", "result", "run_started", "resume", "approve_decision", "run_summary"}:
            validator("journal-line").validate(json.loads(dump_journal_line(parse_journal_line(json.dumps(ex)))))
