"""Domain-specific exceptions for typed error handling at boundaries."""

from __future__ import annotations


class ConfigurationError(Exception):
    """Missing, invalid, or incomplete configuration.

    Raised when required configuration values are absent, malformed, or
    logically inconsistent. Typically caught at CLI boundaries to provide
    user-friendly error messages.

    Example:
        >>> from agentdag.domain.errors import ConfigurationError
        >>> err = ConfigurationError("No SMTP hosts configured")
        >>> str(err)
        'No SMTP hosts configured'
    """


class DeliveryError(Exception):
    """Email/notification delivery failed at SMTP level.

    Raised when all configured SMTP hosts fail to accept the message.
    Contains details about the delivery failure for logging and user feedback.

    Example:
        >>> from agentdag.domain.errors import DeliveryError
        >>> err = DeliveryError("Connection refused by smtp.example.com:587")
        >>> str(err)
        'Connection refused by smtp.example.com:587'
    """


class InvalidRecipientError(ValueError):
    """Email address validation failure.

    Raised when a recipient address fails RFC 5321/5322 validation.
    Inherits from ValueError so existing ``except ValueError`` handlers
    continue to catch it during the migration period.

    Example:
        >>> from agentdag.domain.errors import InvalidRecipientError
        >>> err = InvalidRecipientError("Invalid email: not-an-email")
        >>> str(err)
        'Invalid email: not-an-email'
        >>> isinstance(err, ValueError)
        True
    """


class KernelError(Exception):
    """Base of the coordinator kernel's typed errors.

    Example:
        >>> from agentdag.domain.errors import KernelError
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
    """Control flow, not an error: an approve node has no decision yet, the coordinator exits (design 3.4).

    ``payload_hash`` names WHICH payload the run is waiting on, because a decision is
    recorded per (node id, payload hash): one node suspending twice under a CHANGED payload
    asks two different questions, and only the hash tells them apart. ``None`` when the
    raiser had no payload to bind to.

    Example:
        >>> from agentdag.domain.errors import Suspended
        >>> Suspended("a_push_list", payload_hash="sha256:ab").payload_hash
        'sha256:ab'
    """

    def __init__(self, node_id: str, *, payload_hash: str | None = None) -> None:
        super().__init__(node_id)
        self.node_id = node_id
        self.payload_hash = payload_hash


__all__ = [
    "ConfigurationError",
    "DeliveryError",
    "InvalidRecipientError",
    "KernelError",
    "LockHeld",
    "NondeterministicCallError",
    "RunRefused",
    "SpecRejected",
    "Suspended",
    "WorkflowNotFound",
]
