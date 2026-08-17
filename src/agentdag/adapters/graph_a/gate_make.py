"""GatePort: ``make test`` (or an injected command) under ONE host-wide file lock.

The gate is the mechanical step the agent cannot satisfy by asserting that it did the
work: it is a separate process and the coordinator reads only its exit code. The lock
exists because the bmk tool environment is shared across the whole host, so two gates
running at once can rebuild it under each other.

Contents:
    * :class:`MakeTestGate` - the port implementation.
"""

from __future__ import annotations

import subprocess  # nosec B404 - running the gate as a separate process IS this adapter
from typing import TYPE_CHECKING

from filelock import FileLock

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = ["MakeTestGate"]


class MakeTestGate:
    """Run a project's test gate and report its exit code."""

    def __init__(self, *, lock: Path, command: Sequence[str] = ("make", "test")) -> None:
        """Store the lock path and the command to run.

        Args:
            lock: Path of the host-wide lock file serialising every gate run.
            command: The gate command; the default is the project's ``make test``.
        """
        self._lock = lock
        self._command = tuple(command)

    def run(self, worktree: Path, log: Path) -> int:
        """Run the gate under the host-wide lock; return its exit code.

        Args:
            worktree: The working tree to run the gate in.
            log: File the combined stdout and stderr are written to.

        Returns:
            The gate's exit code; ``0`` means the change passed.
        """
        # Suppressions below: the gate command comes from the composition root, never user text.
        with FileLock(str(self._lock)):
            proc = subprocess.run(  # nosec B603  # noqa: S603
                list(self._command),
                cwd=worktree,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(proc.stdout + proc.stderr)
        return proc.returncode
