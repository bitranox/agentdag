"""RED/GREEN tests for the credential probe and the re-labelling it drives.

The provider's CLI reports an exhausted quota and a rejected credential identically, so
the ONLY thing that separates them is the status code an out-of-band request comes back
with. These tests drive that mapping through the probe's own ``send`` seam - no network,
no credential - and then prove the executor re-labels on a positive answer and ONLY on a
positive answer.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.credential_probe import ApiCredentialProbe, NoCredentialProbe, status_of
from agentdag.adapters.kernel.executor_claude import ClaudeExecutor, CredentialCopy, OAuthTokenFile
from agentdag.domain.models import CredentialVerdict, ErrorType, NodeError, NodeOutcome, NodeStatus

if TYPE_CHECKING:
    from pathlib import Path


class FixedProbe:
    """A probe that answers one fixed verdict and counts how often it was asked."""

    def __init__(self, verdict: CredentialVerdict) -> None:
        self.verdict_to_give = verdict
        self.asks = 0

    async def verdict(self) -> CredentialVerdict:
        """Record the ask and answer."""
        self.asks += 1
        return self.verdict_to_give


def failed_with(error_type: ErrorType) -> NodeOutcome:
    """Build a FAILED outcome carrying ``error_type``."""
    return NodeOutcome(
        status=NodeStatus.FAILED,
        executor_used="claude",
        model_used="sonnet",
        effort_used="-",
        error=NodeError(type=error_type, message="Not logged in - Please run /login", transient=False),
    )


def executor_with(probe: object, tmp_path: Path) -> ClaudeExecutor:
    """Build an executor whose credential probe is ``probe``."""
    keyfile = tmp_path / "token"
    keyfile.write_text("t", encoding="utf-8")
    return ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), credential_probe=probe)  # type: ignore[arg-type]


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, CredentialVerdict.RATE_LIMITED),
        (401, CredentialVerdict.UNAUTHORIZED),
        (403, CredentialVerdict.UNAUTHORIZED),
        (200, CredentialVerdict.INDETERMINATE),
        (500, CredentialVerdict.INDETERMINATE),
    ],
)
def test_the_api_probe_maps_each_status_to_its_verdict(status: int, expected: CredentialVerdict) -> None:
    """200 is INDETERMINATE on purpose: a credential that works NOW does not explain a
    refusal that already happened, so it is evidence for neither verdict.
    """
    probe = ApiCredentialProbe(read_token=lambda: "tok", send=lambda _request, _timeout: status)

    assert asyncio.run(probe.verdict()) is expected


@pytest.mark.os_agnostic
def test_the_api_probe_reports_no_evidence_when_it_cannot_ask() -> None:
    """A probe that cannot reach the provider has learned nothing, and must not raise:
    it runs in a failure path, and a raise would turn a failed diagnosis into a second
    unrelated failure recorded against the node.
    """

    def unreachable(_request: urllib.request.Request, _timeout: float) -> int:
        raise urllib.error.URLError("no route to host")

    probe = ApiCredentialProbe(read_token=lambda: "tok", send=unreachable)

    assert asyncio.run(probe.verdict()) is CredentialVerdict.INDETERMINATE


@pytest.mark.os_agnostic
def test_the_api_probe_does_not_ask_without_a_token() -> None:
    """No token is no evidence - and asking anyway would send an unauthenticated request
    that comes back 401, manufacturing an UNAUTHORIZED verdict out of a missing keyfile.
    """
    asked = []

    def record(request: urllib.request.Request, _timeout: float) -> int:
        asked.append(request)
        return 401

    probe = ApiCredentialProbe(read_token=lambda: None, send=record)

    assert asyncio.run(probe.verdict()) is CredentialVerdict.INDETERMINATE
    assert asked == []


@pytest.mark.os_agnostic
def test_the_api_probe_sends_the_credential_as_a_bearer_token_to_the_messages_endpoint() -> None:
    """What is actually sent matters: a probe hitting the wrong endpoint or omitting the
    version header gets a 4xx that has nothing to do with quota, and reads as a verdict.
    """
    sent: list[urllib.request.Request] = []

    def capture(request: urllib.request.Request, _timeout: float) -> int:
        sent.append(request)
        return 429

    probe = ApiCredentialProbe(read_token=lambda: "sk-secret", send=capture)
    asyncio.run(probe.verdict())

    request = sent[0]
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer sk-secret"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert json.loads(request.data or b"{}")["max_tokens"] == 1


@pytest.mark.os_agnostic
def test_the_no_op_probe_never_claims_a_verdict() -> None:
    """The default must not guess: an unwired probe reporting RATE_LIMITED would suspend
    runs on evidence nobody gathered.
    """
    assert asyncio.run(NoCredentialProbe().verdict()) is CredentialVerdict.INDETERMINATE


@pytest.mark.os_agnostic
def test_status_of_returns_a_refusals_code_rather_than_raising_it() -> None:
    """urllib raises HTTPError for 4xx, but a 429 IS this probe's answer, so the helper
    has to hand the code back instead of letting it propagate.
    """
    request = urllib.request.Request("https://example.invalid", method="POST")

    def raise_429(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.HTTPError("https://example.invalid", 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    original = urllib.request.urlopen
    urllib.request.urlopen = raise_429  # type: ignore[assignment]
    try:
        assert status_of(request, 1.0) == 429
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]


@pytest.mark.os_agnostic
def test_an_auth_shaped_failure_is_relabelled_when_the_probe_says_rate_limited(tmp_path: Path) -> None:
    executor = executor_with(FixedProbe(CredentialVerdict.RATE_LIMITED), tmp_path)

    refined = asyncio.run(executor._separated_refusal(failed_with(ErrorType.AUTH_FAILURE)))  # noqa: SLF001

    assert refined.error is not None
    assert refined.error.type is ErrorType.RATE_LIMITED
    assert refined.error.message == "Not logged in - Please run /login"  # the provider's own text survives
    assert refined.status is NodeStatus.FAILED


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "verdict", [CredentialVerdict.UNAUTHORIZED, CredentialVerdict.INDETERMINATE]
)
def test_an_auth_shaped_failure_stands_when_the_probe_does_not_say_rate_limited(
    verdict: CredentialVerdict, tmp_path: Path
) -> None:
    """The upgrade happens on positive evidence only. INDETERMINATE is the arm that matters:
    a probe that could not ask must leave the classification exactly where it was.
    """
    executor = executor_with(FixedProbe(verdict), tmp_path)

    refined = asyncio.run(executor._separated_refusal(failed_with(ErrorType.AUTH_FAILURE)))  # noqa: SLF001

    assert refined.error is not None
    assert refined.error.type is ErrorType.AUTH_FAILURE


@pytest.mark.os_agnostic
def test_a_failure_that_is_not_auth_shaped_is_never_probed(tmp_path: Path) -> None:
    """Probing costs a request against a metered endpoint, so it must fire only on the one
    classification it can possibly change - not on every failed node.
    """
    probe = FixedProbe(CredentialVerdict.RATE_LIMITED)
    executor = executor_with(probe, tmp_path)

    refined = asyncio.run(executor._separated_refusal(failed_with(ErrorType.EXECUTOR_ERROR)))  # noqa: SLF001

    assert refined.error is not None
    assert refined.error.type is ErrorType.EXECUTOR_ERROR
    assert probe.asks == 0


@pytest.mark.os_agnostic
def test_a_successful_outcome_is_never_probed(tmp_path: Path) -> None:
    probe = FixedProbe(CredentialVerdict.RATE_LIMITED)
    executor = executor_with(probe, tmp_path)
    done = NodeOutcome(status=NodeStatus.DONE, executor_used="claude", model_used="s", effort_used="-")

    assert asyncio.run(executor._separated_refusal(done)) is done  # noqa: SLF001
    assert probe.asks == 0


@pytest.mark.os_agnostic
def test_the_keyfile_credential_offers_its_contents_as_the_bearer_token(tmp_path: Path) -> None:
    keyfile = tmp_path / "token"
    keyfile.write_text("  sk-abc\n", encoding="utf-8")

    assert OAuthTokenFile(keyfile).bearer_token() == "sk-abc"


@pytest.mark.os_agnostic
def test_the_keyfile_credential_offers_no_token_when_it_cannot_be_read(tmp_path: Path) -> None:
    assert OAuthTokenFile(tmp_path / "absent").bearer_token() is None


@pytest.mark.os_agnostic
def test_the_credentials_file_credential_digs_out_its_access_token(tmp_path: Path) -> None:
    source = tmp_path / ".credentials.json"
    source.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-xyz"}}), encoding="utf-8")

    assert CredentialCopy(source).bearer_token() == "sk-xyz"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "payload",
    ["not json at all", "[]", '{"claudeAiOauth": {}}', '{"claudeAiOauth": "wrong shape"}', '{"other": 1}'],
)
def test_the_credentials_file_credential_offers_no_token_for_a_shape_it_does_not_know(
    payload: str, tmp_path: Path
) -> None:
    """A guess about an unrecognised credential shape is not evidence, so every one of
    these is None rather than a partially-read string.
    """
    source = tmp_path / ".credentials.json"
    source.write_text(payload, encoding="utf-8")

    assert CredentialCopy(source).bearer_token() is None
