"""RED/GREEN tests for the kernel domain: records, journal lines, the content-addressed key."""

from __future__ import annotations

import json

import pytest

from agentdag.domain.journal import (
    ApproveDecisionLine,
    PlanAcceptedLine,
    PlanInvalidatedLine,
    ResultLine,
    StartedLine,
    SubtreeDoneLine,
    dump_journal_line,
    parse_journal_line,
)
from agentdag.domain.kernel_errors import Suspended
from agentdag.domain.keys import canonical_json, content_hash, hash8, journal_key, prefix_hash, record_hash
from agentdag.domain.models import (
    Budget,
    Decision,
    ErrorType,
    Isolation,
    Kind,
    NodeError,
    NodeOutcome,
    NodeSpec,
    NodeStatus,
    ResultRecord,
    TierRole,
    Tokens,
)


def spec(**over: object) -> NodeSpec:
    base: dict[str, object] = {
        "node_id": "w_migrate@1",
        "kind": Kind.WORK,
        "brief_ref": "nodes/w_migrate@1/00000000/brief.md",
        "executor": "claude",
        "tier_role": TierRole.STANDARD,
        "write_set": ["wt/r/**"],
        "requires": [],
        "isolation": Isolation.WORKTREE,
        "deps": ["g_discover"],
        "deadline_s": 3600,
        "budget": Budget(tokens={"sonnet": 400_000}),
        "attempt": 0,
    }
    base.update(over)
    return NodeSpec.model_validate(base)


def record(node_id: str = "g_discover", status: NodeStatus = NodeStatus.DONE) -> ResultRecord:
    return ResultRecord(
        node_id=node_id,
        attempt=0,
        status=status,
        artefact_refs=[],
        key_facts={"n": 2},
        typed_fields=["n"],
        tokens=None,
        charged_tokens={},
        cost_usd=None,
        duration_s=0.1,
        executor_used="code",
        model_used="-",
        effort_used="-",
        knowledge_used=[],
        input_hash="sha256:0",
    )


def test_canonical_json_is_sorted_compact_and_stable() -> None:
    assert canonical_json({"b": 1, "a": [2, {"d": None, "c": "x"}]}) == '{"a":[2,{"c":"x","d":null}],"b":1}'


def test_journal_key_ignores_limits_and_display_but_not_inputs() -> None:
    k = journal_key(
        spec(), brief_hash=content_hash("brief"), input_hash=content_hash("input"), prefix=prefix_hash([record()])
    )
    assert k.startswith("v2:sha256:") and len(hash8(k)) == 8
    same = journal_key(
        spec(deadline_s=1, budget=Budget(tokens={"sonnet": 1})),
        brief_hash=content_hash("brief"),
        input_hash=content_hash("input"),
        prefix=prefix_hash([record()]),
    )
    assert same == k  # deadline and budget are limits, not identity
    for changed in (
        spec(attempt=1),
        spec(continuation=1),
        spec(model="opus"),
        spec(write_set=["wt/other/**"]),
        spec(isolation=Isolation.DIR),
        spec(compact={"trigger_tokens": 1000, "keep_last_n": 4}),
    ):
        assert (
            journal_key(
                changed,
                brief_hash=content_hash("brief"),
                input_hash=content_hash("input"),
                prefix=prefix_hash([record()]),
            )
            != k
        )
    assert (
        journal_key(
            spec(), brief_hash=content_hash("brief2"), input_hash=content_hash("input"), prefix=prefix_hash([record()])
        )
        != k
    )
    assert (
        journal_key(
            spec(),
            brief_hash=content_hash("brief"),
            input_hash=content_hash("input"),
            prefix=prefix_hash([record(status=NodeStatus.FAILED)]),
        )
        != k
    )
    # deps is NOT identity on its own: a dependency's contribution is its RESULT, carried
    # by `prefix` (each record_hash already embeds its own node_id) - so two specs naming
    # different deps but hashed against the SAME prefix must collide, not diverge.
    same_prefix = prefix_hash([record()])
    assert journal_key(
        spec(deps=["a"]), brief_hash=content_hash("brief"), input_hash=content_hash("input"), prefix=same_prefix
    ) == journal_key(
        spec(deps=["b"]), brief_hash=content_hash("brief"), input_hash=content_hash("input"), prefix=same_prefix
    )


def test_record_hash_is_content_addressed() -> None:
    assert record_hash(record()) == record_hash(record()) and record_hash(record()) != record_hash(record(node_id="x"))


def test_journal_line_round_trips_and_is_one_line() -> None:
    line = StartedLine(key="v2:sha256:" + "0" * 64, node_id="n", attempt=0, at="2026-08-17T09:12:03+00:00")
    text = dump_journal_line(line)
    assert "\n" not in text and parse_journal_line(text) == line
    result = ResultLine(key=line.key, record=record(), at="2026-08-17T09:12:41+00:00")
    assert json.loads(dump_journal_line(result))["record"]["tokens"] is None
    with pytest.raises(ValueError):
        parse_journal_line('{"event": "nope", "at": "2026-08-17T09:12:03+00:00"}')


def test_timestamps_must_be_utc_with_explicit_offset() -> None:
    with pytest.raises(ValueError):
        StartedLine(key="v2:sha256:" + "0" * 64, node_id="n", attempt=0, at="2026-08-17T09:12:03Z")


def test_tokens_serialise_the_schema_field_name_in() -> None:
    assert Tokens(**{"in": 1, "out": 2, "cache_read": 3, "reasoning": 0}).model_dump(by_alias=True) == {
        "in": 1,
        "out": 2,
        "cache_read": 3,
        "reasoning": 0,
    }


def test_node_error_carries_the_closed_vocabulary() -> None:
    assert NodeError(type=ErrorType.AGENTS_EMPTY_RESULT, message="", transient=False).type == "agents_empty_result"
    with pytest.raises(ValueError):
        NodeError.model_validate({"type": "oops", "message": "", "transient": False})


def test_node_outcome_defaults_are_the_empty_shapes() -> None:
    o = NodeOutcome(status=NodeStatus.DONE, executor_used="code", model_used="-", effort_used="-")
    assert (o.artefact_refs, o.key_facts, o.typed_fields, o.charged_tokens, o.tokens, o.error) == (
        [],
        {},
        [],
        {},
        None,
        None,
    )


def test_suspended_names_the_node() -> None:
    assert Suspended("a_push_list").node_id == "a_push_list"


def test_decision_requires_a_payload_hash() -> None:
    # A decision that names no payload has half an identity - there is no legacy,
    # hash-less shape to fall back to any more. model_validate (not the keyword
    # constructor) exercises the field MISSING entirely, which a keyword call cannot
    # express and still type-check under pyright strict.
    d = Decision(node_id="a", decision="hold", by="me", token_id="local", payload_hash="sha256:0")
    assert d.payload_hash == "sha256:0"
    with pytest.raises(ValueError, match="payload_hash"):
        Decision.model_validate({"node_id": "a", "decision": "hold", "by": "me", "token_id": "local"})
    with pytest.raises(ValueError, match="payload_hash"):
        Decision(node_id="a", decision="hold", by="me", token_id="local", payload_hash="")


def test_approve_decision_line_requires_a_payload_hash() -> None:
    at = "2026-08-17T09:12:03+00:00"
    line = ApproveDecisionLine(
        node_id="a", decision="hold", reason="", by="me", token_id="local", payload_hash="sha256:0", at=at
    )
    assert line.payload_hash == "sha256:0"
    with pytest.raises(ValueError, match="payload_hash"):
        ApproveDecisionLine.model_validate(
            {"node_id": "a", "decision": "hold", "reason": "", "by": "me", "token_id": "local", "at": at}
        )


def test_the_three_replan_lines_parse_back_to_their_own_types() -> None:
    """Task 35 step 5. Each new line must survive its own DEFAULT dump.

    Dumped with NO arguments, because that is what production does: this repo has already
    shipped a field whose default dump its own schema rejected, caught only because one test
    passed ``by_alias=True`` and the other's data never reached the branch.
    """
    key = "v2:sha256:" + "0" * 64
    at = "2026-08-17T09:12:03+00:00"
    lines = (
        PlanAcceptedLine(key=key, node_id="p", entries=3, at=at),
        PlanInvalidatedLine(key=key, node_id="p", reasons=("g.rc == 1",), at=at),
        SubtreeDoneLine(key=key, node_id="p", done=True, at=at),
    )

    for line in lines:
        text = line.model_dump_json()
        assert "\n" not in text
        assert parse_journal_line(text) == line


def test_an_invalidated_plan_keeps_every_reason_rather_than_one_summary() -> None:
    """The next planner is briefed with these, so a flattened summary cannot be acted on.

    The same rule ``NotPlanned.reasons`` and ``SubPlanRefused.reasons`` already follow: a
    planner told about the first of four mistakes fixes one and is refused again.
    """
    line = PlanInvalidatedLine(
        key="v2:sha256:" + "0" * 64,
        node_id="p",
        reasons=("entry 'x' names unregistered op 'teleport'", "done_when references an unknown node"),
        at="2026-08-17T09:12:03+00:00",
    )

    parsed = parse_journal_line(line.model_dump_json())

    assert isinstance(parsed, PlanInvalidatedLine)
    assert len(parsed.reasons) == 2
    assert "teleport" in parsed.reasons[0]
