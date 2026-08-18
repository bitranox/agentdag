"""Domain-specific exceptions for typed error handling at boundaries.

The coordinator kernel's own family lives next door in
:mod:`agentdag.domain.kernel_errors`; nothing here is part of it.

Contents:
    * :class:`ConfigurationError` - missing, invalid or incomplete configuration.
    * :class:`DeliveryError` - email delivery failed at the SMTP level.
    * :class:`InvalidRecipientError` - an address failed RFC 5321/5322 validation.
"""

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


__all__ = [
    "ConfigurationError",
    "DeliveryError",
    "InvalidRecipientError",
]
