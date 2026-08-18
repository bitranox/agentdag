"""NoScope: a plain child process, for a host with no systemd user manager (design C8, Task 17).

The fallback :class:`~agentdag.application.kernel.ports.Scope`: no cgroup, no unit, no
``systemctl``. :func:`~agentdag.composition.kernel.wire_kernel` picks this whenever
:class:`~agentdag.adapters.kernel.scope_systemd.SystemdScope` cannot be used (not Linux,
``systemd-run`` missing, or the user manager itself is not up).

``is_alive``/``kill`` only work for a handle THIS instance's own :meth:`NoScope.start`
returned: the port carries no promise of surviving a process restart, and the CLI never
needs that here - within one invocation, the same :class:`NoScope` instance is the one
that started the process and the one asked about it (a persisted registry across process
boundaries is M3's cancel/deadline work, out of this task's scope).

Contents:
    * :class:`NoScope` - the port implementation.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - launching the coordinator process IS this adapter
import time
from typing import TYPE_CHECKING

from ...application.kernel.ports import LaunchResult, ScopeHandle

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = ["NoScope"]

_TERM_GRACE_S = 5.0
"""How long :meth:`NoScope.kill` waits for a SIGTERM'd process before escalating to SIGKILL."""

_POLL_INTERVAL_S = 0.05

_LOG_NAME = "launch.log"
_LOG_TAIL_BYTES = 8192
"""How much of ``launch.log`` :meth:`NoScope.confirm` reads back as a failure's stderr -
enough for a real diagnostic, bounded so a coordinator that ran a while before dying
never hands the CLI megabytes to print."""


class NoScope:
    """Scope port over a plain child process (``subprocess.Popen``): no cgroup, no unit."""

    def __init__(self) -> None:
        """Start with no tracked processes."""
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def start(self, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> ScopeHandle:
        """Launch ``argv`` as a plain child process and remember it under ``unit``.

        Args:
            unit: A name for this process; only used to look it up again in
                :meth:`is_alive`/:meth:`kill` - no OS-level unit is created.
            argv: The command to run.
            env: The child's environment, used AS GIVEN (never merged with this
                process's own - the caller decides exactly what the child inherits).
            cwd: The child's working directory.

        Returns:
            A handle naming ``unit``, the child's pid, and where its stdout/stderr
            were redirected (``cwd / "launch.log"``, append, owner-only).
        """
        log_path = cwd / _LOG_NAME
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(log_fd, "ab") as log_fh:
            proc = subprocess.Popen(  # nosec B603  # noqa: S603
                argv, env=dict(env), cwd=cwd, stdout=log_fh, stderr=subprocess.STDOUT
            )
        self._processes[unit] = proc
        return ScopeHandle(unit=unit, pid=proc.pid, log_path=log_path)

    def confirm(self, handle: ScopeHandle, *, timeout_s: float) -> LaunchResult:
        """Poll the process :meth:`start` launched for this handle, up to ``timeout_s``.

        See :meth:`~agentdag.application.kernel.ports.Scope.confirm` for the contract.
        """
        proc = self._processes.get(handle.unit)
        if proc is None:
            return LaunchResult(alive=False, stderr="no process was started for this handle")
        deadline = time.monotonic() + timeout_s
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL_S)
        returncode = proc.poll()
        if returncode is None or returncode == 0:
            return LaunchResult(alive=True, stderr="")
        return LaunchResult(alive=False, stderr=_read_log_tail(handle.log_path))

    def is_alive(self, handle: ScopeHandle) -> bool:
        """Return whether the process :meth:`start` launched for this handle is still running.

        Returns:
            ``False`` for a handle this instance never started - there is nothing to
            poll, so it cannot be alive by this instance's own knowledge.
        """
        proc = self._processes.get(handle.unit)
        if proc is None:
            return False
        return proc.poll() is None

    def kill(self, handle: ScopeHandle) -> bool:
        """SIGTERM the process, escalate to SIGKILL after :data:`_TERM_GRACE_S`.

        Returns:
            ``True`` once the process is confirmed gone (or was never tracked, or had
            already exited); ``False`` if it is still alive after the SIGKILL (a
            process that ignores even ``SIGKILL`` is impossible in userspace, but one
            still uninterruptible-sleeping in the kernel past the grace period is not,
            and :meth:`Popen.wait` raises :class:`subprocess.TimeoutExpired` rather
            than returning in that case - caught here so this method keeps its
            documented ``bool`` contract instead of letting that exception escape).
        """
        proc = self._processes.get(handle.unit)
        if proc is None:
            return True
        if proc.poll() is not None:
            return True
        proc.terminate()
        deadline = time.monotonic() + _TERM_GRACE_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(_POLL_INTERVAL_S)
        proc.kill()
        try:
            proc.wait(timeout=_TERM_GRACE_S)
        except subprocess.TimeoutExpired:
            return False
        return proc.poll() is not None


def _read_log_tail(path: Path) -> str:
    """Return ``path``'s last :data:`_LOG_TAIL_BYTES`, or ``""`` if it has nothing yet."""
    if not path.is_file():
        return ""
    return path.read_bytes()[-_LOG_TAIL_BYTES:].decode("utf-8", errors="replace")
