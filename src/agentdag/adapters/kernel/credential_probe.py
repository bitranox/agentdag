"""Credential probes: asking the provider why it refused, when its CLI will not say.

The Claude CLI reports an exhausted subscription quota and a genuinely rejected credential
identically - the same "Not logged in - Please run /login" text, the same
``authentication_failed`` error, a null ``api_error_status``. Measured 2026-08-24 on SDK
0.2.144: three dispatches failed that way while the same credential returned HTTP 429
``rate_limit_error`` from the API in the same minute. No substring list can separate them,
because the discriminator never reaches the caller.

These implement :class:`~agentdag.application.kernel.ports.CredentialProbe`:

    * :class:`NoCredentialProbe` - the default: learns nothing, on purpose.
    * :class:`ApiCredentialProbe` - one request against the Messages API, read for the
      status code the CLI discarded.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ...application.kernel.ports import ProbeFinding
from ...domain.models import CredentialVerdict

if TYPE_CHECKING:  # pragma: no cover - annotations only; `from __future__` keeps them strings
    from collections.abc import Callable

__all__ = ["ApiCredentialProbe", "NoCredentialProbe", "status_of"]


_VERDICT_BY_STATUS: dict[int, CredentialVerdict] = {
    401: CredentialVerdict.UNAUTHORIZED,
    403: CredentialVerdict.UNAUTHORIZED,
    429: CredentialVerdict.RATE_LIMITED,
}
"""The only status codes that CARRY a verdict; every other one is no evidence.

429 is the whole reason this module exists. 401 and 403 both mean the provider will not
accept this identity, which is the answer an operator has to act on differently. A 200 is
deliberately absent: it says the credential works NOW, which does not explain a refusal
that already happened, so it must not be reported as either verdict.
"""

_EXPECTED_UNMAPPED = {
    200: "the credential works now, which does not explain a refusal that already happened",
}
"""Statuses that carry no verdict but are not a sign of anything wrong with the probe.

Kept apart from the genuinely unrecognised ones so the two do not read alike: 200 is a
DESIGNED outcome of asking, while a 404 means the probe is asking the wrong question - a
retired model id, a moved endpoint - and needs somebody to look at the probe itself."""

_ANTHROPIC_VERSION = "2023-06-01"
"""The API version header the Messages endpoint requires; unrelated to the model used."""


class NoCredentialProbe:
    """The probe that always answers "no evidence", which is what having no probe means.

    The default wherever a :class:`~agentdag.application.kernel.ports.CredentialProbe` is
    optional, so an executor built without one classifies exactly as it did before probes
    existed. Deliberately not a stub that guesses: an unwired probe reporting
    ``RATE_LIMITED`` would suspend runs on evidence nobody gathered.
    """

    async def examine(self) -> ProbeFinding:
        """Report that nothing was learned, because nothing was asked."""
        return ProbeFinding(verdict=CredentialVerdict.INDETERMINATE, detail="no credential probe wired")


def status_of(request: urllib.request.Request, timeout_s: float) -> int:
    """Send ``request`` and return its HTTP status, treating an error status as an answer.

    ``urllib`` raises :class:`~urllib.error.HTTPError` for 4xx and 5xx, but a 429 IS the
    result this probe wants, so the error's own ``code`` is returned rather than propagated.

    Args:
        request: The prepared request to send.
        timeout_s: How long to wait before giving up.

    Returns:
        The HTTP status code the provider answered with.

    Raises:
        ValueError: ``request`` is not https - refused before anything is sent, since the
            bearer token attached to it must not leave over a scheme that cannot encrypt it.
        urllib.error.URLError: the request never reached a provider that could answer.
    """
    scheme = urllib.parse.urlparse(request.full_url).scheme
    if scheme != "https":
        # B310's own remedy, enforced rather than annotated: urlopen honours file:, ftp: and
        # custom schemes, so an endpoint that reached this dataclass from configuration could
        # otherwise turn a credential check into a local file read. A bearer token is attached
        # to this request, so the scheme also decides whether it goes out encrypted.
        msg = f"credential probe endpoint must be https, not {scheme!r}"
        raise ValueError(msg)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310  # nosec B310
            return int(response.status)
    except urllib.error.HTTPError as refused:
        return int(refused.code)


@dataclass(frozen=True)
class ApiCredentialProbe:
    """Asks the Messages API directly, and reads the status code the CLI threw away.

    Spends one request of the smallest shape the endpoint accepts. That is the point rather
    than a regrettable cost: a quota refusal is only observable by ASKING for quota, so a
    cheaper check that does not touch the metered path could not answer the question. When
    quota is exhausted the request is refused at 429 and nothing is charged for it.

    Attributes:
        read_token: Where the bearer token comes from - normally the executor's own
            credential source, so the probe asks about exactly the credential that failed.
            Returning ``None`` means the token could not be produced, which is no evidence.
        endpoint: The Messages endpoint to ask.
        model: The model named in the probe request. Any valid id works; the cheapest is
            used because the response body is discarded unread.
        timeout_s: How long to wait. Short on purpose - this runs in a failure path that a
            node is already blocked on, and a slow answer is worth less than a fast
            ``INDETERMINATE``.
        send: The seam :meth:`verdict` sends through, so the mapping from status to verdict
            is testable without a network or a credential.
    """

    read_token: Callable[[], str | None]
    endpoint: str = "https://api.anthropic.com/v1/messages"
    model: str = "claude-haiku-4-5-20251001"
    timeout_s: float = 10.0
    send: Callable[[urllib.request.Request, float], int] = field(default=status_of)

    async def examine(self) -> ProbeFinding:
        """Ask the provider, off the event loop, and map its status to a verdict.

        :func:`urllib.request.urlopen` blocks, and this runs inside the coordinator's own
        loop while other nodes may still be streaming, so it goes through
        :func:`asyncio.to_thread` rather than stalling them for the timeout.
        """
        return await asyncio.to_thread(self._examine)

    def _examine(self) -> ProbeFinding:
        """Do the blocking ask. Never raises: every failure to learn is INDETERMINATE.

        Each way of learning nothing keeps its own ``detail``, because they are not the same
        problem: an UNMAPPED status means the probe itself has gone wrong - a retired model
        id answering 404, a moved endpoint - and by verdict alone that is indistinguishable
        from a healthy timeout, so it would silently restore the defect this class exists to
        fix, in exactly the misreporting way the original defect did.
        """
        token = self.read_token()
        if token is None:
            return ProbeFinding(verdict=CredentialVerdict.INDETERMINATE, detail="no token to ask with")
        try:
            status = self.send(self._request(token), self.timeout_s)
        except Exception as unreachable:
            return ProbeFinding(
                verdict=CredentialVerdict.INDETERMINATE,
                detail=f"could not reach the provider ({type(unreachable).__name__})",
            )
        verdict = _VERDICT_BY_STATUS.get(status)
        if verdict is not None:
            return ProbeFinding(verdict=verdict, detail=f"http {status}")
        expected = _EXPECTED_UNMAPPED.get(status)
        if expected is not None:
            return ProbeFinding(verdict=CredentialVerdict.INDETERMINATE, detail=f"http {status}, {expected}")
        return ProbeFinding(
            verdict=CredentialVerdict.INDETERMINATE,
            detail=f"http {status}, which this probe does not recognise - check the probe, not the credential",
        )

    def _request(self, token: str) -> urllib.request.Request:
        """Build the smallest well-formed Messages request that still exercises quota."""
        body = json.dumps(
            {"model": self.model, "max_tokens": 1, "messages": [{"role": "user", "content": "."}]}
        ).encode("utf-8")
        return urllib.request.Request(  # noqa: S310 - the endpoint is this dataclass's own field, https by default
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "authorization": f"Bearer {token}",
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )
