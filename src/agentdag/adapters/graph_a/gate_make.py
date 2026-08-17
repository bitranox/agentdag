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

from filelock import FileLock, Timeout

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = ["MakeTestGate"]

_DEFAULT_LOCK_TIMEOUT_S = 3600.0
"""How long a gate call waits for the host-wide lock before giving up (M1 leftover): long
enough for a real ``make test`` run under contention, short enough that a wedged holder is
reported rather than hung on forever."""


class MakeTestGate:
    """Run a project's test gate and report its exit code."""

    def __init__(
        self, *, lock: Path, command: Sequence[str] = ("make", "test"), timeout: float = _DEFAULT_LOCK_TIMEOUT_S
    ) -> None:
        """Store the lock path, the command to run, and how long to wait for the lock.

        Args:
            lock: Path of the host-wide lock file serialising every gate run.
            command: The gate command; the default is the project's ``make test``.
            timeout: Seconds to wait for ``lock`` before raising; the bmk tool
                environment is shared across the whole host, so a held lock is a real
                condition to report, not something to wait out forever.
        """
        self._lock = lock
        self._command = tuple(command)
        self._timeout = timeout

    def run(self, worktree: Path, log: Path) -> int:
        """Run the gate under the host-wide lock; return its exit code.

        Args:
            worktree: The working tree to run the gate in.
            log: File the combined stdout and stderr are written to.

        Returns:
            The gate's exit code; ``0`` means the change passed.

        Raises:
            RuntimeError: the lock is still held by another gate after ``timeout``
                seconds - named by its path, so the operator knows which lock to look at
                rather than watching the process hang.
        """
        # Suppressions below: the gate command comes from the composition root, never user text.
        try:
            with FileLock(str(self._lock), timeout=self._timeout):
                proc = subprocess.run(  # nosec B603  # noqa: S603
                    list(self._command),
                    cwd=worktree,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
        except Timeout as exc:
            raise RuntimeError(f"gate lock {self._lock} held for more than {self._timeout}s") from exc
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(proc.stdout + proc.stderr)
        return proc.returncode
