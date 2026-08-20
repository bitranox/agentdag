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

Teardown reaches GRANDCHILDREN on POSIX and only the launched process on Windows. On
POSIX the child is started in a session of its own (``start_new_session=True``) and
:meth:`NoScope.kill` signals the whole PROCESS GROUP, so a coordinator that spawned a
gate, a git command or an executor does not leave them behind. Windows has no process
group to signal through :mod:`subprocess`, so there the kill still reaches exactly one
process; the systemd scope's cgroup is the only teardown in this version that reaps a
whole tree unconditionally.

Contents:
    * :class:`NoScope` - the port implementation.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - launching the coordinator process IS this adapter
import sys
import time
from typing import TYPE_CHECKING

from ...application.kernel.ports import ScopeHandle
from .scope_common import LOG_NAME, confirm_launch

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ...application.kernel.ports import LaunchResult

__all__ = ["NoScope"]

_TERM_GRACE_S = 5.0
"""How long :meth:`NoScope.kill` waits for a SIGTERM'd process before escalating to SIGKILL."""

_POLL_INTERVAL_S = 0.05

_POSIX = sys.platform != "win32"
"""Whether this host has process groups to start the child in and to signal."""


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
        log_path = cwd / LOG_NAME
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(log_fd, "ab") as log_fh:
            # start_new_session on POSIX makes the child a session and PROCESS GROUP leader,
            # with its pid as the group id - which is the handle kill() needs to reach the
            # coordinator's own children (a gate, a git command, an executor) rather than
            # only the process launched here. It also detaches the child from the CLI's
            # terminal, so a Ctrl-C in the operator's shell no longer reaches a background run.
            proc = subprocess.Popen(  # nosec B603  # noqa: S603
                argv, env=dict(env), cwd=cwd, stdout=log_fh, stderr=subprocess.STDOUT, start_new_session=_POSIX
            )
        self._processes[unit] = proc
        return ScopeHandle(unit=unit, pid=proc.pid, log_path=log_path)

    def confirm(self, handle: ScopeHandle, *, timeout_s: float) -> LaunchResult:
        """Poll the process :meth:`start` launched for this handle, up to ``timeout_s``.

        See :meth:`~agentdag.application.kernel.ports.Scope.confirm` for the contract; the
        polling is :func:`~agentdag.adapters.kernel.scope_common.confirm_launch`, shared
        with :class:`~agentdag.adapters.kernel.scope_systemd.SystemdScope`.
        """
        return confirm_launch(
            self._processes.get(handle.unit),
            log_path=handle.log_path,
            timeout_s=timeout_s,
            poll_interval_s=_POLL_INTERVAL_S,
        )

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
        """SIGTERM the process group, escalate to SIGKILL after :data:`_TERM_GRACE_S`.

        On POSIX both signals go to the whole PROCESS GROUP the child leads (see the
        module docstring), so a coordinator's own children - a gate subprocess, a git
        command, an executor - go with it instead of surviving as orphans. On Windows
        there is no group to signal and only the launched process is terminated.

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
        _signal_tree(proc, terminate=True)
        deadline = time.monotonic() + _TERM_GRACE_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(_POLL_INTERVAL_S)
        _signal_tree(proc, terminate=False)
        try:
            proc.wait(timeout=_TERM_GRACE_S)
        except subprocess.TimeoutExpired:
            return False
        return proc.poll() is not None


def _signal_tree(proc: subprocess.Popen[bytes], *, terminate: bool) -> None:
    """Signal ``proc``'s whole process group on POSIX, or just ``proc`` on Windows.

    The group id is ``proc.pid`` because :meth:`NoScope.start` made the child a group
    leader. Two errors are expected and ignored, and both mean the same thing - there is
    nothing left in the group to signal, which is the outcome the caller wanted:

    * ``ProcessLookupError`` (ESRCH): the child exited between the liveness check and this
      call, and its group is gone.
    * ``PermissionError`` (EPERM): the child has exited but not been reaped, so the group
      holds only a zombie. Linux answers ESRCH (or succeeds) there; macOS and the BSDs
      answer EPERM, which uncaught escaped :meth:`NoScope.kill`, a method documented to
      return a ``bool`` (measured on macOS CI, every Python version, 2026-08-18). EPERM
      cannot mean "not ours" here: this adapter only ever signals a group it created
      itself in :meth:`NoScope.start`.

    The platform test is written as ``sys.platform == "win32"`` inline rather than
    through :data:`_POSIX`, because that is the form the type checker narrows on: with
    ``--pythonplatform Windows`` everything after the early return is then unreachable,
    and ``os.killpg``/``signal.SIGKILL`` (which do not exist there) are never analysed.

    Args:
        proc: The process this instance started.
        terminate: ``True`` for SIGTERM (the polite first ask), ``False`` for SIGKILL.
    """
    if sys.platform == "win32":
        if terminate:
            proc.terminate()
        else:
            proc.kill()
        return
    sig = signal.SIGTERM if terminate else signal.SIGKILL
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)
