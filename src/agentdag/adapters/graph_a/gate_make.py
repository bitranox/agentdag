"""GatePort: ``make test`` (or an injected command) under ONE host-wide file lock.

The gate is the mechanical step the agent cannot satisfy by asserting that it did the
work: it is a separate process and the coordinator reads only its exit code. The lock
exists because the bmk tool environment is shared across the whole host, so two gates
running at once can rebuild it under each other.

Contents:
    * :data:`GATE_ENV_ALLOWLIST` - the only environment variables a gate process gets.
    * :func:`gate_env` - that allowlist intersected with an environment.
    * :class:`MakeTestGate` - the port implementation.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - running the gate as a separate process IS this adapter
from typing import TYPE_CHECKING

from filelock import FileLock, Timeout

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = ["GATE_ENV_ALLOWLIST", "MakeTestGate", "gate_env"]

_DEFAULT_LOCK_TIMEOUT_S = 3600.0
"""How long a gate call waits for the host-wide lock before giving up (M1 leftover): long
enough for a real ``make test`` run under contention, short enough that a wedged holder is
reported rather than hung on forever."""

_GATE_LOG_MODE = 0o600
"""``gate.log`` is a store file like every other one under the run directory: created
owner-only BY CONSTRUCTION rather than at whatever the platform umask happens to be."""

GATE_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TEMP",
    "TMP",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "SYSTEMROOT",
    "USERPROFILE",
    "COMSPEC",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "UV_CACHE_DIR",
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
    "UV_PYTHON",
    "UV_OFFLINE",
    "BMK_PYTHON_CMD",
    "BMK_OUTPUT_FORMAT",
)
"""The ONLY environment a gate subprocess gets - never the coordinator's whole environment.

Built from what the shipped gate command actually reads. ``make test`` is bmk: the
Makefile shells out to ``uv tool dir`` / ``uv tool install``, so ``PATH``, ``HOME`` (uv's
cache and tool directories hang off it, as do the ``XDG_*`` overrides of those) and the
proxy and CA variables a download needs are all load-bearing; ``BMK_PYTHON_CMD`` and
``BMK_OUTPUT_FORMAT`` are bmk's own knobs; ``SYSTEMROOT``, ``COMSPEC``, ``PATHEXT``,
``TEMP``/``TMP``, ``APPDATA``/``LOCALAPPDATA`` and ``USERPROFILE`` are what a Windows
child needs to start at all (a child handed an environment without ``SYSTEMROOT`` dies
in Winsock with empty output rather than an error).

What is deliberately NOT here matters as much:

* ``XDG_RUNTIME_DIR`` and ``DBUS_SESSION_BUS_ADDRESS``. They belong to the operator's
  login session, and with them a ``systemd-run --user --scope`` started from inside the
  gate would create a unit under the USER manager - outside the run's own scope, so it
  would outlive the run's teardown. The gate has no business talking to the session bus.
* ``VIRTUAL_ENV``. An inherited one makes bmk resolve its tooling from the wrong
  environment and report a green or red that is not this worktree's.
* Anything credential-shaped. This is an allowlist, so a variable is absent unless it is
  named here, and no name here contains ``TOKEN``, ``KEY`` or ``SECRET``.
"""


def gate_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Intersect ``environ`` with :data:`GATE_ENV_ALLOWLIST`, keeping the allowlist's values.

    Args:
        environ: The environment to filter, normally :data:`os.environ`.

    Returns:
        Only the allowlisted names ``environ`` actually holds, with their values.

    Example:
        >>> gate_env({"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-secret"})
        {'PATH': '/usr/bin'}
    """
    return {key: environ[key] for key in GATE_ENV_ALLOWLIST if key in environ}


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

        The subprocess gets an EXPLICIT environment, :func:`gate_env` of this process's
        own - never the coordinator's whole environment by inheritance. Which side the
        coordinator was started from (an operator's interactive shell under
        ``--foreground``, or a background scope) then stops changing what the gate sees.

        Args:
            worktree: The working tree to run the gate in.
            log: File the combined stdout and stderr are written to, owner-only.

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
                    env=gate_env(os.environ),
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
        except Timeout as exc:
            raise RuntimeError(f"gate lock {self._lock} held for more than {self._timeout}s") from exc
        _write_owner_only(log, proc.stdout + proc.stderr)
        return proc.returncode


def _write_owner_only(log: Path, text: str) -> None:
    """Write ``text`` to ``log``, creating it ``0600`` by construction.

    ``Path.write_text`` would create the file at the platform default minus the umask,
    which on a host with a permissive umask leaves a gate log - a file that holds a
    build's whole stdout and stderr - group- or world-readable. Opening with an explicit
    mode closes that window instead of chmod-ing after the fact.

    The encoding is explicit: the output was DECODED as utf-8 by ``subprocess.run``, so
    writing it back through the machine's locale codec can raise UnicodeEncodeError
    (cp1252 on a Windows runner) inside the gate body, which the kernel would record as a
    red gate - a content failure that never happened.

    Args:
        log: The file to write; its parent directories are created if missing.
        text: The gate's combined stdout and stderr.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _GATE_LOG_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
