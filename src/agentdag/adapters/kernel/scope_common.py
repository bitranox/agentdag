"""What both :class:`~agentdag.application.kernel.ports.Scope` adapters do identically.

:mod:`agentdag.adapters.kernel.scope_systemd` and
:mod:`agentdag.adapters.kernel.scope_none` differ in how a process is STARTED and
STOPPED - a transient systemd unit versus a plain child - but they confirm a launch the
same way (poll the process this instance started, up to a deadline, and on an early
non-zero exit read its log back as the diagnostic). That shared half lives here so the
two cannot drift into answering ``confirm`` differently.

Contents:
    * :func:`read_log_tail` - the last few KiB of a launch log, decoded leniently.
    * :func:`confirm_launch` - poll one started process into a
      :class:`~agentdag.application.kernel.ports.LaunchResult`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ...application.kernel.ports import LaunchResult

if TYPE_CHECKING:
    import subprocess  # nosec B404 - a TYPE-ONLY import; the two scope adapters do the launching
    from pathlib import Path

__all__ = ["LOG_NAME", "LOG_TAIL_BYTES", "confirm_launch", "read_log_tail"]

LOG_NAME = "launch.log"
"""The file, inside the run directory, a launch's stdout and stderr are redirected to."""

LOG_TAIL_BYTES = 8192
"""How much of :data:`LOG_NAME` a failed launch reports back as its stderr - enough for a
real diagnostic, bounded so a coordinator that ran a while before dying never hands the
CLI megabytes to print."""


def read_log_tail(path: Path) -> str:
    """Return ``path``'s last :data:`LOG_TAIL_BYTES`, or ``""`` if it has nothing yet.

    Args:
        path: The launch log to read.

    Returns:
        The tail, decoded as UTF-8 with undecodable bytes replaced - the tail can start
        mid-character, and a diagnostic must never fail to print.
    """
    if not path.is_file():
        return ""
    return path.read_bytes()[-LOG_TAIL_BYTES:].decode("utf-8", errors="replace")


def confirm_launch(
    process: subprocess.Popen[bytes] | None, *, log_path: Path, timeout_s: float, poll_interval_s: float
) -> LaunchResult:
    """Poll one started process up to ``timeout_s`` and report whether the launch took.

    Still running when the deadline passes proves the launch took, and so does a clean
    exit inside the window (a run whose whole workload finished that fast). Only an
    EARLY NON-ZERO exit means it never started, and then the log holds the reason.

    Args:
        process: The process the caller's own ``start`` created for this handle, or
            ``None`` when this instance started nothing under that name.
        log_path: Where that process's stdout and stderr were redirected.
        timeout_s: How long to wait for the launch to prove itself.
        poll_interval_s: How long to sleep between polls.

    Returns:
        The launch result; its ``stderr`` carries the log tail only on a failure.
    """
    if process is None:
        return LaunchResult(alive=False, stderr="no process was started for this handle")
    deadline = time.monotonic() + timeout_s
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(poll_interval_s)
    returncode = process.poll()
    if returncode is None or returncode == 0:
        return LaunchResult(alive=True, stderr="")
    return LaunchResult(alive=False, stderr=read_log_tail(log_path))
