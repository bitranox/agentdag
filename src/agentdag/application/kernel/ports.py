"""Ports the coordinator kernel needs: the clock, the journal, the run lock, the executor, the scope.

Every effect the kernel has on the world - reading the time, recording what happened,
holding the run directory, running a node, starting or killing the OS-level unit a
node runs under - goes through one of these seams (design 3.1, 3.3, 3.4, C8), so the
coordinator itself stays a deterministic program over typed records.

Contents:
    * :class:`Clock` - the ONE seam the kernel reads wall-clock time through.
    * :func:`stamp` - render a clock reading as the journal's timestamp format.
    * :class:`Journal` - the append-only, replayable log of what a run has done.
    * :class:`RunLock` - the run directory's exclusive lock.
    * :class:`LockToken` - proof of a held lock, returned by :meth:`RunLock.acquire`.
    * :class:`ExecutorRequest` - everything an :class:`Executor` needs to run one node.
    * :class:`Executor` - runs one node's dispatch and reports its outcome.
    * :class:`Scope` - starts, probes and kills the OS-level unit a node runs under.
    * :class:`ScopeHandle` - identifies a unit a :class:`Scope` started.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from ...domain.journal import JournalLine
    from ...domain.models import LockHolder, NodeOutcome

__all__ = [
    "Clock",
    "Executor",
    "ExecutorRequest",
    "Journal",
    "LockToken",
    "RunLock",
    "Scope",
    "ScopeHandle",
    "stamp",
]


class Clock(Protocol):
    """The ONE seam the kernel reads wall-clock time through (design 3.3, O19)."""

    def now(self) -> datetime:
        """Return the current instant, tz-aware in UTC."""
        ...


def stamp(clock: Clock) -> str:
    """Render ``clock``'s current reading as the journal's timestamp format.

    Args:
        clock: The clock to read.

    Returns:
        ``YYYY-MM-DDTHH:MM:SS+00:00`` - seconds precision, an explicit UTC offset,
        never a trailing ``Z`` (design 3.3, O19; matches the pattern journal lines
        validate their ``at`` field against).

    Raises:
        ValueError: ``clock.now()`` is naive, or not UTC.

    Example:
        >>> from datetime import datetime, timezone
        >>> class _FixedClock:
        ...     def now(self) -> datetime:
        ...         return datetime(2026, 8, 17, 9, 12, 3, tzinfo=timezone.utc)
        >>> stamp(_FixedClock())
        '2026-08-17T09:12:03+00:00'
    """
    now = clock.now()
    if now.tzinfo != timezone.utc:
        raise ValueError(f"clock reading is not UTC: {now!r}")
    return now.isoformat(timespec="seconds")


class Journal(Protocol):
    """The append-only, replayable log of what a run has done (design 3.1)."""

    def append(self, line: JournalLine) -> None:
        """Append ``line`` under the single-writer O_APPEND discipline; also copy it to the audit log."""
        ...

    def lines(self) -> list[JournalLine]:
        """Return every line the journal holds, parsed and typed, in file order."""
        ...


class RunLock(Protocol):
    """The run directory's exclusive lock: at most one live coordinator per run (design 3.4)."""

    def acquire(self, run_dir: Path, holder: LockHolder) -> LockToken:
        """Take the lock for ``run_dir``.

        Args:
            run_dir: The run directory to lock.
            holder: This process's identity, recorded as the lock's owner.

        Returns:
            Proof of the held lock.

        Raises:
            LockHeld: another live coordinator already holds ``run_dir``.
        """
        ...

    def release(self, token: LockToken) -> None:
        """Release a lock this process holds."""
        ...


@dataclass(frozen=True, slots=True)
class LockToken:
    """Proof of a held run-directory lock, handed back by :meth:`RunLock.acquire`."""

    path: Path
    holder: LockHolder


@dataclass(frozen=True, slots=True)
class ExecutorRequest:
    """Everything an :class:`Executor` needs to run one node's dispatch (design 2.1, C8)."""

    node_dir: Path
    cwd: Path
    brief: str
    prompt: str
    model: str
    effort: str | None
    max_turns: int
    isolation_root: Path
    write_set: tuple[str, ...]
    deny_bash: tuple[str, ...]


class Executor(Protocol):
    """Runs one node's dispatch against a prepared request and reports its outcome."""

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Run ``request`` to completion (or a suspend/needs-context outcome) and report it."""
        ...


class Scope(Protocol):
    """Starts, probes and kills the OS-level unit a node's executor runs under."""

    def start(self, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> ScopeHandle:
        """Start ``argv`` under a new scope named ``unit`` and return its handle."""
        ...

    def is_alive(self, handle: ScopeHandle) -> bool:
        """Return whether the unit ``handle`` names still has live processes."""
        ...

    def kill(self, handle: ScopeHandle) -> bool:
        """Kill the unit ``handle`` names.

        Returns:
            ``True`` only once the cgroup (or process) is verified gone.
        """
        ...


@dataclass(frozen=True, slots=True)
class ScopeHandle:
    """Identifies a unit a :class:`Scope` started."""

    unit: str
    pid: int
