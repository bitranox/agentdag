"""The coordinator kernel's typed errors, and the control-flow signal that is not one.

Kept apart from :mod:`agentdag.domain.errors` (the email and configuration errors this
package shipped before the kernel existed) because the two families answer to different
callers and share nothing: an ``except KernelError`` in the run loop must not widen the
day it catches a delivery failure, and a mail adapter must not be able to raise
something the kernel treats as its own.

The whole family is one base plus its subclasses, so a caller can catch the family or
one member. :class:`Suspended` is deliberately NOT one of them: it is how an approve
node hands control back to the operator, not a failure, and every ``except`` that means
"the run broke" must let it pass.

Contents:
    * :class:`KernelError` - base of the family; a dispatch failing on one is recorded
      as NON-transient (a configuration or program bug reproduces on a retry).
    * :class:`LockHeld`, :class:`NondeterministicCallError`, :class:`WorkflowNotFound`,
      :class:`SpecRejected`, :class:`RunRefused` - the members.
    * :class:`Suspended` - control flow: an approve node has no decision yet.
"""

from __future__ import annotations

from .models import SuspendReason

__all__ = [
    "KernelError",
    "LockHeld",
    "NondeterministicCallError",
    "RunRefused",
    "SpecRejected",
    "Suspended",
    "WorkflowNotFound",
]


class KernelError(Exception):
    """Base of the coordinator kernel's typed errors.

    Example:
        >>> from agentdag.domain.kernel_errors import KernelError
        >>> issubclass(KernelError, Exception)
        True
    """


class LockHeld(KernelError):
    """Another live coordinator holds this run dir's lock."""


class NondeterministicCallError(KernelError):
    """A workflow module reaches for the clock or randomness; that breaks resume (design 3.3)."""


class WorkflowNotFound(KernelError):
    """No built-in workflow of that name."""


class SpecRejected(KernelError):
    """Whole-spec validation refused a node (design 2.4)."""


class RunRefused(KernelError):
    """run.start / resume refused before anything ran (missing runs dir, live lock, bad args)."""


class Suspended(Exception):
    """Control flow, not an error: the run hands control back and the coordinator exits (design 3.4).

    ``payload_hash`` names WHICH payload the run is waiting on, because a decision is
    recorded per (node id, payload hash): one node suspending twice under a CHANGED payload
    asks two different questions, and only the hash tells them apart. ``None`` when the
    raiser had no payload to bind to.

    ``reason`` says what the run is waiting FOR, which is not the same question as which
    node it stopped at. It defaults to :attr:`~agentdag.domain.models.SuspendReason.DECISION`
    because an approve node was the only raiser for as long as this class existed, so every
    existing raise site means exactly that; a raiser waiting on something other than a person
    has to say so.

    Example:
        >>> from agentdag.domain.kernel_errors import Suspended
        >>> Suspended("a_push_list", payload_hash="sha256:ab").payload_hash
        'sha256:ab'
        >>> Suspended("a_push_list").reason.value
        'decision'
    """

    def __init__(
        self,
        node_id: str,
        *,
        payload_hash: str | None = None,
        reason: SuspendReason = SuspendReason.DECISION,
    ) -> None:
        """Bind the suspending node's id, the payload hash it waits on, and what it waits for.

        Args:
            node_id: The node the run stopped at.
            payload_hash: The payload the run is waiting for an answer on, or ``None``.
            reason: What the run is waiting for - a person, or the provider's quota.
        """
        super().__init__(node_id)
        self.node_id = node_id
        self.payload_hash = payload_hash
        self.reason = reason
