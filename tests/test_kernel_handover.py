"""RED/GREEN tests for the handover stop-notice texts (design 3.8, decision 14).

The wording of these two strings is not a style choice, it is a measured requirement.
`RESEARCH/workflow/design/probes/handover-nudge-inject.md` ran 40 dispatches and found
that a notice CLAIMING the node is near its context ceiling is refused 4 times out of 4
(the node reads its own remaining budget and says the claim is false), while a notice
asserting only a coordinator DECISION, against a brief that pre-authorises it, is obeyed
4 times out of 4.

So the negative tests here carry as much weight as the positive ones: they are what stops
a later edit from quietly putting the refuted framing back.
"""

from __future__ import annotations

import pytest

from agentdag.domain.handover import HANDOVER_FILENAME, prompt_with_stop_duty, stop_notice

# Words that assert a checkable FACT about the node's context. A notice built from any of
# these invites the node to test the claim against its own telemetry, which is exactly what
# it did, four times out of four.
CEILING_CLAIM_WORDS = ("ceiling", "approaching", "running out", "nearly full", "context limit", "tokens remaining")


@pytest.mark.os_agnostic
def test_prompt_with_stop_duty_keeps_the_original_prompt() -> None:
    """The duty is added to the node's task, never a replacement for it."""
    composed = prompt_with_stop_duty("Migrate the mail adapter.", handover_path="nodes/w1/handover.json")
    assert "Migrate the mail adapter." in composed


@pytest.mark.os_agnostic
def test_prompt_with_stop_duty_names_where_the_handover_goes() -> None:
    """A node told to hand over must know the path, or it invents one."""
    composed = prompt_with_stop_duty("Do the thing.", handover_path="nodes/w1/abcd1234/handover.json")
    assert "nodes/w1/abcd1234/handover.json" in composed


@pytest.mark.os_agnostic
def test_prompt_with_stop_duty_pre_authorises_the_notice_without_verification() -> None:
    """The measured requirement: the task itself says the notice is authoritative.

    Without this the notice arrives with no standing and is treated as prompt injection.
    """
    composed = prompt_with_stop_duty("Do the thing.", handover_path="h.json").lower()
    assert "authoritative" in composed
    assert "verify" in composed


@pytest.mark.os_agnostic
def test_prompt_with_stop_duty_makes_no_claim_about_the_node_s_context() -> None:
    """Decision 14: the DUTY must not be conditioned on a ceiling either.

    Measured, a duty reading "if you are told you are approaching your context ceiling"
    made the node check the precondition against its own budget and refuse - 0 of 4 - even
    when the notice itself carried no claim.
    """
    composed = prompt_with_stop_duty("Do the thing.", handover_path="h.json").lower()
    for word in CEILING_CLAIM_WORDS:
        assert word not in composed, f"the duty must not mention {word!r} (decision 14)"


@pytest.mark.os_agnostic
def test_stop_notice_names_the_handover_path() -> None:
    """The notice repeats the path, so a node that lost the duty from context still complies."""
    assert "nodes/w1/abcd1234/handover.json" in stop_notice(handover_path="nodes/w1/abcd1234/handover.json")


@pytest.mark.os_agnostic
def test_stop_notice_asserts_a_decision_not_a_fact() -> None:
    """The notice says the coordinator is ending the turn; it claims nothing about the node."""
    notice = stop_notice(handover_path="h.json").lower()
    assert "run coordinator" in notice
    assert "stop" in notice


@pytest.mark.os_agnostic
def test_stop_notice_makes_no_claim_the_node_can_check_and_refute() -> None:
    """Decision 14, the load-bearing negative.

    This is the assertion that would have caught design 3.8's original wording, which was
    refused 4 of 4.
    """
    notice = stop_notice(handover_path="h.json").lower()
    for word in CEILING_CLAIM_WORDS:
        assert word not in notice, f"the stop notice must not mention {word!r} (decision 14)"


@pytest.mark.os_agnostic
def test_handover_filename_is_json_because_the_record_is_typed() -> None:
    """The successor's brief is built from typed fields, so the node writes JSON, not prose."""
    assert HANDOVER_FILENAME.endswith(".json")
