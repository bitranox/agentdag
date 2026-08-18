"""Tests for the two ``Scope`` port implementations: a plain child process, a systemd user scope.

:class:`~agentdag.adapters.kernel.scope_none.NoScope` is exercised everywhere
(``os_agnostic``); :class:`~agentdag.adapters.kernel.scope_systemd.SystemdScope` needs a
real Linux host with a live ``systemd --user`` manager, so it is ``os_linux`` and
``local_only`` - CI runs with ``-m "not local_only"`` and never sees it (measured live on
``lxc-pydev``, S0 probe, ``workflow/design/probes/s0-systemd-scopes.md`` in RESEARCH:
``systemd-run --user --scope --unit=NAME`` names the resulting unit ``NAME.scope``, which
is exactly what :class:`SystemdScope` returns as ``ScopeHandle.unit``).

Also covers (fix round 1): :meth:`NoScope.confirm`'s two outcomes over a REAL child
process (a long-lived one, and one that exits non-zero with a message on stderr), and
:func:`~agentdag.composition.kernel.manager_state_is_live`'s pure decision table (the
scope-selection probe :func:`~agentdag.composition.kernel._choose_scope` keys on).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentdag.adapters.kernel.scope_none import NoScope
from agentdag.adapters.kernel.scope_systemd import SystemdScope
from agentdag.composition.kernel import manager_state_is_live

_SLEEP_ARGV = [sys.executable, "-c", "import time; time.sleep(30)"]
_FAIL_ARGV = [sys.executable, "-c", "import sys; print('boom: bad argv', file=sys.stderr); sys.exit(3)"]
_IGNORE_SIGTERM_ARGV = [
    sys.executable,
    "-c",
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
]
"""Ignores SIGTERM so ``NoScope.kill`` must escalate to SIGKILL (a real process cannot
ignore that one) - the only way to reach its ``Popen.wait`` call at all."""
_SPAWNS_A_CHILD_ARGV = [
    sys.executable,
    "-c",
    "import subprocess, sys, time; "
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)']); "
    "print(child.pid, flush=True); "
    "time.sleep(300)",
]
"""A coordinator stand-in that spawns one long-lived GRANDCHILD and prints its pid.

The pid reaches the test through ``launch.log``, which is where ``NoScope.start``
redirects the child's stdout - so the test learns it the same way an operator would,
without reaching inside the adapter."""

_START_GRACE_S = 2.0
"""How long to wait after ``start`` before asserting the process is actually up."""

_REAP_TIMEOUT_S = 5.0
"""How long to wait for a killed grandchild to be reaped by its new parent."""

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


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no process group for subprocess to signal")
def test_noscope_kill_reaps_a_grandchild_not_only_the_process_it_started(tmp_path: Path) -> None:
    """Teardown must reach the coordinator's OWN children, not just the coordinator.

    A real coordinator spawns a gate subprocess, git commands and executors. Signalling
    only the one ``Popen`` leaves every one of them running after a cancel or a deadline,
    holding the gate lock and the worktrees of a run that is supposed to be over.
    """
    scope = NoScope()
    handle = scope.start(unit="agentdag-noscope-grandchild", argv=_SPAWNS_A_CHILD_ARGV, env={}, cwd=tmp_path)
    grandchild = _grandchild_pid(handle.log_path)
    assert _pid_exists(grandchild)  # control: it really is running before the kill

    assert scope.kill(handle) is True

    assert not _pid_exists(handle.pid)
    assert _waits_until_gone(grandchild)


def _grandchild_pid(log_path: Path) -> int:
    """Read the pid the started process printed into its launch log, waiting for it to appear."""
    deadline = time.monotonic() + _START_GRACE_S * 2
    while time.monotonic() < deadline:
        text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        if text.strip():
            return int(text.split()[0])
        time.sleep(0.05)
    raise AssertionError(f"the started process printed no grandchild pid into {log_path}")


def _waits_until_gone(pid: int) -> bool:
    """Return whether ``pid`` disappears within :data:`_REAP_TIMEOUT_S`.

    Polled rather than checked once: a killed process is briefly a zombie until whatever
    inherited it after its own parent died gets round to reaping it.
    """
    deadline = time.monotonic() + _REAP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.os_agnostic
def test_noscope_confirm_reports_alive_for_a_long_lived_process(tmp_path: Path) -> None:
    """``confirm`` reports ``alive`` for a process still running once ``timeout_s`` elapses."""
    scope = NoScope()
    handle = scope.start(unit="agentdag-noscope-confirm-alive", argv=_SLEEP_ARGV, env={}, cwd=tmp_path)

    result = scope.confirm(handle, timeout_s=0.3)

    assert result.alive is True
    assert result.stderr == ""
    assert scope.kill(handle) is True


@pytest.mark.os_agnostic
def test_noscope_confirm_reports_the_captured_stderr_for_an_early_failure(tmp_path: Path) -> None:
    """``confirm`` reports NOT ``alive`` and the launcher's own stderr for an early non-zero exit."""
    scope = NoScope()
    handle = scope.start(unit="agentdag-noscope-confirm-fails", argv=_FAIL_ARGV, env={}, cwd=tmp_path)

    result = scope.confirm(handle, timeout_s=2.0)

    assert result.alive is False
    assert "boom: bad argv" in result.stderr
    assert handle.log_path.is_file()
    assert "boom: bad argv" in handle.log_path.read_text(encoding="utf-8")


@pytest.mark.os_agnostic
def test_noscope_kill_returns_false_when_the_process_outlives_sigkill(tmp_path: Path) -> None:
    """``kill`` catches ``Popen.wait``'s ``TimeoutExpired`` and returns ``False`` (fix round 1).

    A real process cannot outlive a genuine SIGKILL, so this proves the EXCEPTION-HANDLING
    contract, not a truly unkillable process: the SIGKILL is sent for real (the process
    really dies), but ``Popen.wait`` is patched on this ONE instance - a true external edge
    (subprocess reaping is a kernel-timing detail this test cannot otherwise force) - to
    raise ``TimeoutExpired`` regardless, simulating "still not reaped within the grace
    window" deterministically. Before the fix, that exception propagated uncaught instead
    of ``kill`` returning its documented ``bool``.
    """
    scope = NoScope()
    handle = scope.start(unit="agentdag-noscope-outlives-sigkill", argv=_IGNORE_SIGTERM_ARGV, env={}, cwd=tmp_path)
    time.sleep(_START_GRACE_S)
    # White-box: NoScope exposes no public accessor for the Popen it tracks, and forcing
    # kill()'s TimeoutExpired branch needs the REAL object to patch .wait on (see below) -
    # remove this ignore if a public accessor is ever added for another reason.
    proc = scope._processes[handle.unit]  # pyright: ignore[reportPrivateUsage]
    real_wait = proc.wait

    def _wait_still_expired(timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(cmd=_IGNORE_SIGTERM_ARGV, timeout=timeout or 0.0)

    proc.wait = _wait_still_expired  # type: ignore[method-assign]
    try:
        assert scope.kill(handle) is False
    finally:
        proc.wait = real_wait  # type: ignore[method-assign]
        real_wait()  # actually reap the (genuinely dead) process so no zombie is left behind


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("running\n", True),
        ("degraded\n", True),
        ("", False),
        ("", False),
    ],
    ids=["running-0", "degraded-1", "empty-1", "empty-0"],
)
@pytest.mark.os_agnostic
def test_manager_state_is_live_keys_on_stdout_not_returncode(stdout: str, expected: bool) -> None:
    """The scope-selection probe decides from STDOUT alone; the (unused) exit code names the case."""
    assert manager_state_is_live(stdout) is expected


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


@pytest.mark.os_linux
@pytest.mark.local_only
@pytest.mark.skipif(_SYSTEMD_RUN_MISSING, reason="systemd-run not on PATH")
def test_systemdscope_confirm_reports_the_captured_stderr_for_an_early_failure(tmp_path: Path) -> None:
    """A ``--unit=`` containing ``@`` fails outright (the template-instance separator); ``confirm``
    reports it as a failed launch with ``systemd-run``'s own diagnostic, not a hung wait."""
    scope = SystemdScope()
    handle = scope.start(unit="agentdag-systemdscope-confirm@bad", argv=_SLEEP_ARGV, env=dict(os.environ), cwd=tmp_path)

    result = scope.confirm(handle, timeout_s=2.0)

    assert result.alive is False
    assert result.stderr.strip() != ""


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
