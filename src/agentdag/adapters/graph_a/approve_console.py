"""ApprovePort on the console - attended on purpose.

The baseline asks a human before anything leaves the process. An unattended approve
is a later milestone's job, and its absence here is what makes the cost of adding it
measurable.

Contents:
    * :class:`ConsoleApprove` - the port implementation.
"""

from __future__ import annotations

import rich_click as click

__all__ = ["ConsoleApprove"]


class ConsoleApprove:
    """Ask the operator on the console and return their answer."""

    def confirm(self, prompt: str) -> bool:
        """Show ``prompt`` and return whether the operator approved.

        Args:
            prompt: The listing of what would be pushed.

        Returns:
            ``True`` only if the operator explicitly confirmed; the default is no.
        """
        from ..cli import safe_console  # noqa: PLC0415 - deferred: the cli package imports this one at import time

        safe_console.echo(prompt)
        return click.confirm("approve?", default=False)
