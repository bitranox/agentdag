"""The default notification sink: accept every event and do nothing with it.

What an operator who configured no notification gets, and therefore the sink almost every
run uses. It exists so the kernel never has to hold ``notifier: Notifier | None`` and
branch on it at four emit sites: an optional port is four chances to forget the check,
while a no-op implementation is none.

Contents:
    * :class:`NoNotifier` - the sink that goes nowhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...application.kernel.notify import RunEvent

__all__ = ["NoNotifier"]


class NoNotifier:
    """Accepts a run event and discards it; cannot fail.

    Example:
        >>> from agentdag.application.kernel.notify import RunEvent
        >>> from agentdag.domain.models import RunStatus
        >>> NoNotifier().emit(RunEvent(run_id="r1", workflow="graph-a",
        ...                            status=RunStatus.DONE, at="2026-08-21T14:12:03+00:00"))
    """

    def emit(self, event: RunEvent) -> None:
        """Discard ``event``.

        Args:
            event: The run event nobody asked to be told about.
        """
        del event
