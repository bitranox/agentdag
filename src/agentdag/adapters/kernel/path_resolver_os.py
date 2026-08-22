"""PathResolver: the real filesystem's answer to "where does this path actually lead".

Contents:
    * :class:`OsPathResolver` - the :class:`~agentdag.application.kernel.ports.PathResolver`
      port implementation over :func:`os.path.realpath`.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["OsPathResolver"]


class OsPathResolver:
    """Resolves a path with :func:`os.path.realpath`, which follows symlinks and does not require existence."""

    def resolve(self, path: Path) -> Path:
        """Return ``path`` with every symlink and ``..`` resolved.

        ``os.path.realpath`` rather than :meth:`pathlib.Path.resolve` for one reason worth
        stating: both follow symlinks, and neither raises on a path that does not exist,
        but ``realpath`` is what the shipped PreToolUse write guard already uses
        (``adapters.kernel.hooks_claude``), so a brief the validator accepts and a write
        the hook allows are judged by the same resolution.

        Args:
            path: The path to resolve.

        Returns:
            The resolved path.
        """
        return Path(os.path.realpath(path))
