"""SystemdScope: run the coordinator in a real ``systemd --user`` scope (design C8, Task 17).

Measured live on ``lxc-pydev`` (S0 probe, ``workflow/design/probes/s0-systemd-scopes.md`` in
RESEARCH, re-verified while building this module): ``systemd-run --user --scope --unit=NAME
--collect ARGV`` creates a transient unit named ``NAME.scope`` - systemd appends the ``.scope``
suffix itself when ``--unit`` is given without one - so every later lookup (``systemctl --user
is-active``, the cgroup path) has to use that SAME suffixed name, not the bare one the caller
passed. This adapter appends it once, in :meth:`SystemdScope.start`, and returns a
:class:`~agentdag.application.kernel.ports.ScopeHandle` whose ``unit`` already carries it, so
:meth:`is_alive`/:meth:`kill` never have to guess.

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

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["SystemdScope"]

_ACTIVE = "active"
_CGROUP_ROOT = Path("/sys/fs/cgroup/user.slice")
_CGROUP_POLL_INTERVAL_S = 0.2
_CGROUP_POLL_TIMEOUT_S = 10.0
"""How long :meth:`SystemdScope.kill` polls the cgroup for empty/gone after ``systemctl stop``
(design C8's cancel path budget; matched here since M3's real cancel command will reuse this)."""


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
            A handle naming the full ``<unit>.scope`` unit and the ``systemd-run``
            process's own pid (immediately available; the scoped command's own pid is
            not, since ``systemd-run`` has not necessarily exec'd it yet when this
            returns - the handle is looked up by unit name, not by this pid, in
            :meth:`is_alive`/:meth:`kill`).
        """
        full_unit = f"{unit}.scope"
        proc = subprocess.Popen(  # nosec B603  # noqa: S603 - a resolved executable plus the caller's own argv, never a shell string
            [_resolved("systemd-run"), "--user", "--scope", f"--unit={full_unit}", "--collect", *argv],
            env=dict(env),
            cwd=cwd,
        )
        return ScopeHandle(unit=full_unit, pid=proc.pid)

    def is_alive(self, handle: ScopeHandle) -> bool:
        """Return whether ``systemctl --user is-active`` reports the unit ``active``."""
        result = subprocess.run(  # nosec B603  # noqa: S603 - a resolved executable and a fixed argument list, never a shell string
            [_resolved("systemctl"), "--user", "is-active", handle.unit],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.stdout.strip() == _ACTIVE

    def kill(self, handle: ScopeHandle) -> bool:
        """Stop the unit, then poll its cgroup for empty or gone, up to :data:`_CGROUP_POLL_TIMEOUT_S`.

        Returns:
            ``True`` once ``cgroup.procs`` is confirmed empty (or the cgroup directory
            is gone entirely - systemd removes it once the scope's last process exits),
            never trusting ``systemctl stop``'s own exit code alone.
        """
        subprocess.run(  # nosec B603  # noqa: S603 - a resolved executable and a fixed argument list, never a shell string
            [_resolved("systemctl"), "--user", "stop", handle.unit],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        cgroup = _cgroup_dir(handle.unit)
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
