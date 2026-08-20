"""The system clock: the one adapter the kernel reads wall-clock time through (design 3.3, O19).

Contents:
    * :class:`UtcClock` - :class:`~agentdag.application.kernel.ports.Clock` over the system's UTC wall clock.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["UtcClock"]


class UtcClock:
    """Clock port over the system's wall clock, always read as tz-aware UTC."""

    def now(self) -> datetime:
        """Return the current instant, tz-aware in UTC.

        Example:
            >>> UtcClock().now().tzinfo is timezone.utc
            True
        """
        return datetime.now(timezone.utc)
