"""The run directory's exclusive lock: an O_EXCL file naming the live holder (design 3.4).

At most one live coordinator may hold a run directory at a time. The lock file
records WHO holds it (host, boot id, pid, pid start time) so a later process can
tell a live holder from a stale one left behind by a crash - a bare pid is never
enough, because pids recycle across reboots and across unrelated processes.

Contents:
    * :func:`current_holder` - identify the calling process as a :class:`~agentdag.domain.models.LockHolder`.
    * :func:`holder_is_alive` - whether a recorded holder is still the same live process.
    * :class:`FileRunLock` - :class:`~agentdag.application.kernel.ports.RunLock` over an exclusive-create lock file.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess  # nosec B404 - probing a pid via tasklist IS this adapter's Windows liveness check
import sys
from pathlib import Path

from ...application.kernel.ports import LockToken
from ...domain.errors import LockHeld
from ...domain.models import LockHolder

__all__ = ["FileRunLock", "current_holder", "holder_is_alive"]

_LOCK_MODE = 0o600
_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_LOCK_FILE_NAME = "lock"
_STAT_FIELD_22_OFFSET = 19
"""Index of field 22 (start time) within the fields AFTER ``/proc/<pid>/stat``'s closing
``)`` - that remainder starts at field 3, so field 22 sits at ``22 - 3``."""


def _read_boot_id() -> str:
    """Return this boot's id from ``/proc``, or ``"-"`` off Linux or when unreadable."""
    try:
        return _BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "-"


def _read_pid_start_time(pid: int) -> str:
    """Return ``pid``'s start time (field 22 of ``/proc/<pid>/stat``), or ``"-"`` when unreadable.

    The ``comm`` field (field 2) is parenthesised but can itself contain spaces and
    parentheses, so the only safe split is on the LAST ``)`` in the line; the
    remainder is fields 3 onward, space-separated, so field 22 sits at index
    ``22 - 3 == 19``.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return "-"
    _, _, rest = raw.rpartition(")")
    fields = rest.split()
    if len(fields) <= _STAT_FIELD_22_OFFSET:
        return "-"
    return fields[_STAT_FIELD_22_OFFSET]


def current_holder() -> LockHolder:
    """Identify the calling process as a :class:`~agentdag.domain.models.LockHolder`.

    Returns:
        This process's host, boot id (or ``"-"``), pid and start time (or ``"-"``).
    """
    pid = os.getpid()
    return LockHolder(
        host=socket.gethostname(),
        boot_id=_read_boot_id(),
        pid=pid,
        pid_start_time=_read_pid_start_time(pid),
    )


def _pid_exists_windows(pid: int) -> bool:
    """Return whether ``pid`` is listed by ``tasklist``.

    ``os.kill(pid, 0)`` is not a liveness probe on Windows - it does not raise
    ``ProcessLookupError`` for a dead pid the way POSIX does - so this shells out
    to the one tool that actually enumerates live processes there. The executable
    is resolved to an absolute path because Windows' ``CreateProcess`` searches
    the PARENT process's ``PATH`` rather than the environment handed to the
    child, so a bare name can fail to resolve.
    """
    tasklist = shutil.which("tasklist") or "tasklist"
    # Suppressions below: a resolved executable and a fixed argument list, never a shell string.
    result = subprocess.run(  # nosec B603  # noqa: S603
        [tasklist, "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return f'"{pid}"' in result.stdout


def _pid_exists(pid: int) -> bool:
    """Return whether a process with ``pid`` currently exists, on this platform."""
    if sys.platform == "win32":
        return _pid_exists_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    return True


def holder_is_alive(holder: LockHolder) -> bool:
    """Return whether ``holder`` is still the same live process (design 3.4).

    A bare pid is never enough: pids recycle. ``holder`` counts as alive only if
    its pid exists AND its recorded start time matches the pid's current start
    time - unless the recorded start time is ``"-"`` (unavailable when it was
    captured), in which case existence alone stands in. If the LIVE start time
    cannot be read but a real one was recorded, the two cannot be proven equal,
    so this reports ``False`` rather than trusting a bare pid match.

    Args:
        holder: The recorded holder to check.

    Returns:
        Whether ``holder``'s process is still running and still the same process.
    """
    if not _pid_exists(holder.pid):
        return False
    if holder.pid_start_time == "-":
        return True
    return _read_pid_start_time(holder.pid) == holder.pid_start_time


class FileRunLock:
    """RunLock port over an exclusive-create lock file: at most one live coordinator per run dir."""

    def acquire(self, run_dir: Path, holder: LockHolder) -> LockToken:
        """Take the lock for ``run_dir``, breaking at most one stale lock along the way.

        Args:
            run_dir: The run directory to lock; the lock file is ``run_dir / "lock"``.
            holder: This process's identity, written into the lock file.

        Returns:
            Proof of the held lock.

        Raises:
            LockHeld: another live coordinator already holds ``run_dir``, or the
                lock is still there after breaking one stale holder (a race with
                another process creating it in between).
        """
        path = run_dir / _LOCK_FILE_NAME
        if self._try_create(path, holder):
            return LockToken(path=path, holder=holder)
        self._break_if_stale(path, run_dir)
        if self._try_create(path, holder):
            return LockToken(path=path, holder=holder)
        raise LockHeld(f"run dir {run_dir} is held by another coordinator")

    def release(self, token: LockToken) -> None:
        """Release a lock this process holds; a no-op if the file no longer names this holder."""
        existing = self._read_holder(token.path)
        if existing == token.holder:
            token.path.unlink(missing_ok=True)

    @staticmethod
    def _try_create(path: Path, holder: LockHolder) -> bool:
        """Create ``path`` exclusively and write ``holder`` into it; ``False`` if it already exists."""
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _LOCK_MODE)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(holder.model_dump_json())
        return True

    @staticmethod
    def _read_holder(path: Path) -> LockHolder | None:
        """Read and parse ``path`` as a :class:`LockHolder`; ``None`` if missing or unparseable."""
        try:
            return LockHolder.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _break_if_stale(path: Path, run_dir: Path) -> None:
        """Unlink ``path`` if its recorded holder is gone or unreadable; raise if it is alive.

        Raises:
            LockHeld: the recorded holder is still a live process.
        """
        existing = FileRunLock._read_holder(path)
        if existing is not None and holder_is_alive(existing):
            raise LockHeld(
                f"run dir {run_dir} is held by pid {existing.pid} on {existing.host} since boot {existing.boot_id}"
            )
        path.unlink(missing_ok=True)
