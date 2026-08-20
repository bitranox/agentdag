"""SystemdScope: run the coordinator in a real ``systemd --user`` scope (design C8, Task 17).

Measured live on ``lxc-pydev`` (S0 probe, ``workflow/design/probes/s0-systemd-scopes.md`` in
RESEARCH, re-verified while building this module): ``systemd-run --user --scope --unit=NAME
--collect ARGV`` creates a transient unit named ``NAME.scope`` - systemd appends the ``.scope``
suffix itself when ``--unit`` is given without one - so every later lookup (``systemctl --user
is-active``, the cgroup path) has to use that SAME suffixed name, not the bare one the caller
passed. This adapter appends it once, in :meth:`SystemdScope.start`, and returns a
:class:`~agentdag.application.kernel.ports.ScopeHandle` whose ``unit`` already carries it.

:meth:`is_alive` and :meth:`kill` also normalise :attr:`~agentdag.application.kernel.ports.
ScopeHandle.unit` back onto the suffixed form (:func:`_with_scope_suffix`) before querying
systemd, rather than trusting the handle already carries it: a handle a FRESH process
reconstructs from a bare ``run_id`` alone (``application.kernel.cancel._handle_for``, used by
``run cancel`` and the startup sweep) only ever has the BASE name :func:`~agentdag.application.
kernel.cancel.scope_unit` returns, never having seen this module's own suffixing happen. Without
normalising, ``systemctl --user is-active <base-name>`` resolves the bare name as a ``.service``
that was never created and answers ``inactive`` for a scope that is very much still running
(measured live: ``systemctl --user is-active agentdag-run-doesnotexist`` -> ``inactive``, exit
4) - so a reconstructed handle would silently make :meth:`is_alive`/:meth:`kill` report "already
gone" for a scope neither method ever actually queried.

``systemd-run --scope`` (without ``--no-block``) is SYNCHRONOUS: the ``systemd-run`` process
itself stays alive as long as the scoped command runs, relaying its exit code. This adapter
therefore launches it with ``subprocess.Popen`` (never ``.run()``/``.wait()``) and returns
immediately - the CLI process that called :meth:`start` is free to exit right after, and
``systemd-run`` (now reparented, not signalled by the exiting parent) keeps running detached
under the user's systemd instance, which is exactly what a background ``run start`` needs.

Contents:
    * :class:`SystemdScope` - the port implementation.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - launching the coordinator process (and probing its unit) IS this adapter
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ...application.kernel.ports import ScopeHandle
from .scope_common import LOG_NAME, confirm_launch

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...application.kernel.ports import LaunchResult

__all__ = ["SystemdScope"]

_ACTIVE = "active"
_CGROUP_ROOT = Path("/sys/fs/cgroup/user.slice")
_CGROUP_POLL_INTERVAL_S = 0.2
_CGROUP_POLL_TIMEOUT_S = 10.0
"""How long :meth:`SystemdScope.kill` polls the cgroup for empty/gone after ``systemctl stop``
(design C8's cancel path budget; matched here since M3's real cancel command will reuse this)."""

_CONFIRM_POLL_INTERVAL_S = 0.1


def _with_scope_suffix(unit: str) -> str:
    """Normalise ``unit`` onto the ``.scope``-suffixed form systemd actually uses.

    A no-op for a handle :meth:`SystemdScope.start` returned (already suffixed there).
    Load-bearing for a handle a FRESH process reconstructs from a bare ``run_id`` alone
    (see the module docstring) - without this, :meth:`SystemdScope.is_alive`/:meth:`kill`
    would query a ``.service`` unit that was never created instead of the real ``.scope``
    unit, and report a live scope as already gone.

    Args:
        unit: A handle's ``unit`` - either already ``.scope``-suffixed, or the bare base
            name :func:`~agentdag.application.kernel.cancel.scope_unit` returns.

    Returns:
        ``unit`` unchanged if already suffixed, else ``unit`` with ``.scope`` appended.
    """
    return unit if unit.endswith(".scope") else f"{unit}.scope"


def _resolved(tool: str) -> str:
    """Resolve ``tool`` to an absolute path via ``PATH``, falling back to the bare name.

    Mirrors ``adapters.kernel.lock_file``'s own ``shutil.which(...) or name`` idiom: a
    resolved path is what a security scanner wants to see launching a process, and the
    bare-name fallback keeps this a no-op when the tool is genuinely missing (the caller
    then gets the OS's own "no such file" rather than a swallowed lookup failure).
    """
    return shutil.which(tool) or tool


class SystemdScope:
    """Scope port over ``systemd-run --user --scope``: a real cgroup per run, Linux only."""

    cross_process_capable = True
    """:meth:`is_alive`/:meth:`kill` query systemd/the cgroup filesystem BY UNIT NAME
    alone, never this instance's own :attr:`_processes` - so a handle reconstructed in a
    FRESH process (``run cancel``, the startup sweep) gets a real, truthful answer."""

    def __init__(self) -> None:
        """Start with no tracked processes; :meth:`confirm` polls the SAME instance's own."""
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def start(self, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> ScopeHandle:
        """Start ``argv`` in a new transient user scope named ``unit`` (systemd appends ``.scope``).

        Args:
            unit: The unit's base name, e.g. ``agentdag-run-<run_id>`` - a caller must
                NEVER use ``@`` in it (systemd reads that as the template-instance
                separator and a transient ``--unit=`` name containing one is refused
                outright, "Invalid argument"; see ``adapters.cli.commands.run._scope_unit``,
                which measured this live). systemd always appends ``.scope`` to
                whatever is given, and the returned handle's ``unit`` carries that
                full name.
            argv: The command to run inside the scope.
            env: The child's environment, used AS GIVEN.
            cwd: The child's working directory.

        Returns:
            A handle naming the full ``<unit>.scope`` unit, the ``systemd-run``
            process's own pid (immediately available; the scoped command's own pid is
            not, since ``systemd-run`` has not necessarily exec'd it yet when this
            returns - the handle is looked up by unit name, not by this pid, in
            :meth:`is_alive`/:meth:`kill`), and where its stdout/stderr were
            redirected (``cwd / "launch.log"``, append, owner-only). Since
            ``systemd-run --scope`` stays attached and relays the scoped command's
            exit code (see the module docstring), redirecting its OWN stdout/stderr
            captures both its launch diagnostics (when the scope itself fails to
            start) and the scoped command's own output, in the SAME log - which is
            exactly what :meth:`confirm` reads back on a failed launch.
        """
        full_unit = f"{unit}.scope"
        log_path = cwd / LOG_NAME
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(log_fd, "ab") as log_fh:
            proc = subprocess.Popen(  # nosec B603  # noqa: S603 - a resolved executable plus the caller's own argv, never a shell string
                [_resolved("systemd-run"), "--user", "--scope", f"--unit={full_unit}", "--collect", *argv],
                env=dict(env),
                cwd=cwd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )
        self._processes[full_unit] = proc
        return ScopeHandle(unit=full_unit, pid=proc.pid, log_path=log_path)

    def confirm(self, handle: ScopeHandle, *, timeout_s: float) -> LaunchResult:
        """Poll the ``systemd-run`` process :meth:`start` launched, up to ``timeout_s``.

        ``systemd-run --scope`` (without ``--no-block``) stays attached for as long as
        the scoped command runs (see the module docstring), so "still running after
        ``timeout_s``" proves the scope started - as does a clean exit within the
        window, for a coordinator whose whole run finished in under ``timeout_s``. An
        early NON-zero exit means the scope never started (a bad unit name, no user
        manager) and :data:`~agentdag.adapters.kernel.scope_common.LOG_NAME` holds
        ``systemd-run``'s own diagnostic. The polling itself is
        :func:`~agentdag.adapters.kernel.scope_common.confirm_launch`, shared with
        :class:`~agentdag.adapters.kernel.scope_none.NoScope`.
        """
        return confirm_launch(
            self._processes.get(handle.unit),
            log_path=handle.log_path,
            timeout_s=timeout_s,
            poll_interval_s=_CONFIRM_POLL_INTERVAL_S,
        )

    def is_alive(self, handle: ScopeHandle) -> bool:
        """Return whether ``systemctl --user is-active`` reports the unit ``active``.

        ``handle.unit`` is normalised onto the ``.scope``-suffixed form first
        (:func:`_with_scope_suffix`) so a handle RECONSTRUCTED by a fresh process (``run
        cancel``, the startup sweep) from the bare unit base name is queried the same way
        :meth:`start`'s own handle already is.
        """
        result = subprocess.run(  # nosec B603  # noqa: S603 - a resolved executable and a fixed argument list, never a shell string
            [_resolved("systemctl"), "--user", "is-active", _with_scope_suffix(handle.unit)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.stdout.strip() == _ACTIVE

    def kill(self, handle: ScopeHandle) -> bool:
        """Stop the unit, then poll its cgroup for empty or gone, up to :data:`_CGROUP_POLL_TIMEOUT_S`.

        ``handle.unit`` is normalised the same way :meth:`is_alive` normalises it (see
        there) before either the ``systemctl stop`` or the cgroup path is built from it.

        Returns:
            ``True`` once ``cgroup.procs`` is confirmed empty (or the cgroup directory
            is gone entirely - systemd removes it once the scope's last process exits),
            never trusting ``systemctl stop``'s own exit code alone.
        """
        unit = _with_scope_suffix(handle.unit)
        subprocess.run(  # nosec B603  # noqa: S603 - a resolved executable and a fixed argument list, never a shell string
            [_resolved("systemctl"), "--user", "stop", unit],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        cgroup = _cgroup_dir(unit)
        deadline = time.monotonic() + _CGROUP_POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            if _cgroup_empty(cgroup):
                return True
            time.sleep(_CGROUP_POLL_INTERVAL_S)
        return _cgroup_empty(cgroup)


def _cgroup_dir(unit: str) -> Path:
    """Return this unit's cgroup directory under the user manager's ``app.slice``.

    Raises:
        RuntimeError: not on a POSIX host - :class:`SystemdScope` is Linux-only, and
            ``os.getuid`` does not exist on Windows.
    """
    if sys.platform == "win32":
        raise RuntimeError("SystemdScope requires systemd; it is not available on Windows")
    uid = os.getuid()
    return _CGROUP_ROOT / f"user-{uid}.slice" / f"user@{uid}.service" / "app.slice" / unit


def _cgroup_empty(cgroup: Path) -> bool:
    """Return whether ``cgroup``'s ``cgroup.procs`` lists no processes, or the cgroup is gone.

    systemd removes a scope's cgroup directory once its last process exits, so "gone"
    is the common case, not a failure to distinguish from "empty".
    """
    procs = cgroup / "cgroup.procs"
    if not procs.is_file():
        return True
    return procs.read_text(encoding="utf-8").strip() == ""
