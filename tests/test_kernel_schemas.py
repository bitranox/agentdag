"""Schema conformance: the seam between the design's JSON schemas and the kernel domain models."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from agentdag.domain.journal import dump_journal_line, parse_journal_line
from agentdag.domain.models import ApprovePayload, NodeSpec, ResultRecord

if TYPE_CHECKING:
    from referencing.jsonschema import Schema


class _SchemaValidator(Protocol):
    """The one Draft202012Validator method this test needs, typed.

    jsonschema's shipped stub declares ``validate(self, *args, **kwargs) -> None``,
    which pyright strict reports as partially unknown; this narrow facade defines
    the real signature instead of suppressing the diagnostic at every call site.
    """

    def validate(self, instance: Any) -> None: ...


def load(name: str) -> dict[str, Any]:
    return json.loads((files("agentdag.schemas") / f"{name}.schema.json").read_text())


def validator(name: str) -> _SchemaValidator:
    reg: Registry[Schema] = Registry()
    for other in ("node-spec", "result-record", "journal-line", "approve-payload", "handover", "map-manifest"):
        s = load(other)
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
        reg = reg.with_resource(f"{other}.schema.json", Resource.from_contents(s))
    return cast("_SchemaValidator", Draft202012Validator(load(name), registry=reg))


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
