"""Tests for the two ``Scope`` port implementations: a plain child process, a systemd user scope.

:class:`~agentdag.adapters.kernel.scope_none.NoScope` is exercised everywhere
(``os_agnostic``); :class:`~agentdag.adapters.kernel.scope_systemd.SystemdScope` needs a
real Linux host with a live ``systemd --user`` manager, so it is ``os_linux`` and
``local_only`` - CI runs with ``-m "not local_only"`` and never sees it (measured live on
``lxc-pydev``, S0 probe, ``workflow/design/probes/s0-systemd-scopes.md`` in RESEARCH:
``systemd-run --user --scope --unit=NAME`` names the resulting unit ``NAME.scope``, which
is exactly what :class:`SystemdScope` returns as ``ScopeHandle.unit``).
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from agentdag.adapters.kernel.scope_none import NoScope
from agentdag.adapters.kernel.scope_systemd import SystemdScope

_SLEEP_ARGV = [sys.executable, "-c", "import time; time.sleep(30)"]
_START_GRACE_S = 2.0
"""How long to wait after ``start`` before asserting the process is actually up."""

_SYSTEMD_RUN_MISSING = shutil.which("systemd-run") is None
"""Computed at collection time, so the ``skipif`` below can reference it directly."""


@pytest.mark.os_agnostic
def test_noscope_starts_reports_alive_and_kills_the_process(tmp_path: Path) -> None:
    """A plain child process: alive after start, gone after kill."""
    scope = NoScope()
    handle = scope.start(unit="agentdag-noscope-test", argv=_SLEEP_ARGV, env={}, cwd=tmp_path)
    time.sleep(_START_GRACE_S)

    assert scope.is_alive(handle)

    assert scope.kill(handle) is True
    assert scope.is_alive(handle) is False
    assert not _pid_exists(handle.pid)


@pytest.mark.os_linux
@pytest.mark.local_only
@pytest.mark.skipif(_SYSTEMD_RUN_MISSING, reason="systemd-run not on PATH")
def test_systemdscope_starts_an_active_unit_and_kill_verifies_the_cgroup_empty(tmp_path: Path) -> None:
    """A real ``systemd --user --scope`` unit: active after start, cgroup empty after kill.

    ``systemd-run`` itself (not just the scoped child) refuses to run with an env that
    carries neither ``XDG_RUNTIME_DIR`` nor ``DBUS_SESSION_BUS_ADDRESS`` ("Failed to
    connect to user scope bus via local transport", measured live while building this
    module), so this test passes the current process's own environment rather than an
    empty one - proving the SCOPE mechanism, not the CLI's env allowlist (that is
    ``commands/run.py``'s ``_ENV_ALLOWLIST``, which does carry both).
    """
    scope = SystemdScope()
    handle = scope.start(unit="agentdag-systemdscope-test", argv=_SLEEP_ARGV, env=dict(os.environ), cwd=tmp_path)
    time.sleep(_START_GRACE_S)

    assert handle.unit.endswith(".scope")
    assert scope.is_alive(handle)

    assert scope.kill(handle) is True
    cgroup_procs = _cgroup_procs_path(handle.unit)
    assert not cgroup_procs.is_file() or cgroup_procs.read_text(encoding="utf-8").strip() == ""
    assert scope.is_alive(handle) is False


def _pid_exists(pid: int) -> bool:
    """Return whether a process with ``pid`` currently exists (POSIX liveness probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cgroup_procs_path(unit: str) -> Path:
    """Return the ``cgroup.procs`` path :class:`SystemdScope` itself polls for ``unit``.

    Raises:
        RuntimeError: not on a POSIX host - this test is ``os_linux``, so it never
            runs where ``os.getuid`` would not exist, but the guard keeps this module
            type-checking under every platform pyright analyses.
    """
    if sys.platform == "win32":
        raise RuntimeError("os_linux-only helper called off Linux")
    uid = os.getuid()
    app_slice = Path("/sys/fs/cgroup/user.slice") / f"user-{uid}.slice" / f"user@{uid}.service" / "app.slice"
    return app_slice / unit / "cgroup.procs"
