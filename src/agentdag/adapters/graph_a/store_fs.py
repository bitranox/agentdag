"""RunStore on the filesystem.

One run owns one timestamped directory holding everything it produced::

    <base>/<utc-stamp>/{wt,tally,intents,done,log,home}

The store is where CONTENT lives; the coordinator itself keeps only typed state.

Contents:
    * :class:`FsRunStore` - the port implementation.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["FsRunStore"]

_RUN_DIRS = ("wt", "tally", "intents", "done", "log", "home")


class FsRunStore:
    """A run's directory on disk."""

    def __init__(self, root: Path) -> None:
        """Bind the store to an existing run directory.

        Args:
            root: The run directory; use :meth:`create` to make a fresh one.
        """
        self.root = root

    @classmethod
    def create(cls, base: Path) -> FsRunStore:
        """Create a fresh, timestamped run directory under ``base``.

        Args:
            base: The directory holding every run.

        Returns:
            A store bound to the new run directory.
        """
        root = base / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        for name in _RUN_DIRS:
            (root / name).mkdir(parents=True, exist_ok=True)
        return cls(root)

    def worktree(self, name: str) -> Path:
        """Return the working-tree path for the branch called ``name``."""
        return self.root / "wt" / name

    def log(self, name: str) -> Path:
        """Return the log-file path called ``name``."""
        return self.root / "log" / name

    def home(self, name: str) -> Path:
        """Return (creating it) an isolated home directory for the node called ``name``."""
        path = self.root / "home" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, rel: str, text: str) -> None:
        """Write ``text`` to ``rel`` under the run root, creating parent directories."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def marker(self, key: str) -> Path:
        """Return the done-marker path for ``key``; its existence means already applied."""
        return self.root / "done" / key
