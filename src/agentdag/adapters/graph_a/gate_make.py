"""GatePort: ``make test`` (or an injected command), run as a subprocess.

The gate is the mechanical step the agent cannot satisfy by asserting that it did the
work: it is a separate process and the coordinator reads only its exit code. It gets an
explicit, filtered environment (see :data:`GATE_ENV_ALLOWLIST`) rather than inheriting
the coordinator's whole one.

Contents:
    * :data:`DEFAULT_GATE_COMMAND` - what a gate runs when nothing configures one.
    * :data:`GATE_ENV_ALLOWLIST` - the only environment variables a gate process gets.
    * :func:`gate_env` - that allowlist intersected with an environment.
    * :func:`withheld_names` - what the allowlist dropped, for the gate log's header.
    * :class:`MakeTestGate` - the port implementation.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - running the gate as a separate process IS this adapter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = ["DEFAULT_GATE_COMMAND", "GATE_ENV_ALLOWLIST", "MakeTestGate", "gate_env", "withheld_names"]

DEFAULT_GATE_COMMAND: tuple[str, ...] = ("make", "test")
"""The gate command a caller that names none gets - this project's own test gate.

Declared once, here, and read by both the port's own default and
:func:`~agentdag.composition.kernel.build_op_registry`'s, so the description the planner is
given cannot drift from what an unconfigured gate runs. The value an OPERATOR sees is the
``[kernel] gate_command`` in the packaged ``60-kernel.toml``, which the CLI reads as its
fallback; this is the fallback for a caller that builds a gate without going through config
at all."""

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
    "PYTHONPATH",
    "UV_CACHE_DIR",
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
    "UV_PYTHON",
    "UV_OFFLINE",
    "BMK_PYTHON_CMD",
    "BMK_OUTPUT_FORMAT",
)
"""The ONLY environment a gate subprocess gets - never the coordinator's whole environment.

Built from what a gate command actually reads. The command is an operator's
(``[kernel] gate_command``), so the list covers both the shipped one and an interpreter
invoked directly: ``PYTHONPATH`` is how such a gate reaches a package that is not installed
into it, and ``UV_CACHE_DIR`` is where uv keeps what it would otherwise re-download every
run. The shipped ``make test`` is bmk: the
Makefile shells out to ``uv tool dir`` / ``uv tool install``, so ``PATH``, ``HOME`` (uv's
cache and tool directories hang off it, as do the ``XDG_*`` overrides of those) and the
proxy and CA variables a download needs are all load-bearing; ``BMK_PYTHON_CMD`` and
``BMK_OUTPUT_FORMAT`` are bmk's own knobs; ``SYSTEMROOT``, ``COMSPEC``, ``PATHEXT``,
``TEMP``/``TMP``, ``APPDATA``/``LOCALAPPDATA`` and ``USERPROFILE`` are what a Windows
child needs to start at all (a child handed an environment without ``SYSTEMROOT`` dies
in Winsock with empty output rather than an error).

``PYTHONPATH`` is the one entry that changes which CODE the gate executes rather than only how
it starts, and the recorded argv does not capture it - the environment appears in the gate log's
header and nowhere else. It is the operator's variable, never a node's: a node cannot reach the
coordinator's environment. Read a gate log's header, not just its argv, when a green gate is
surprising.

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


def withheld_names(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Return the sorted names ``environ`` holds that :data:`GATE_ENV_ALLOWLIST` drops.

    NAMES ONLY, never values: the whole point of the allowlist is that some of what it
    drops is a credential, and this goes into a log file.

    Args:
        environ: The environment the gate was filtered from, normally :data:`os.environ`.

    Returns:
        Every name that did not survive the filter, sorted.

    Example:
        >>> withheld_names({"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-secret"})
        ('ANTHROPIC_API_KEY',)
    """
    return tuple(sorted(key for key in environ if key not in GATE_ENV_ALLOWLIST))


class MakeTestGate:
    """Run a project's test gate and report its exit code."""

    def __init__(self, *, command: Sequence[str] = DEFAULT_GATE_COMMAND) -> None:
        """Store the command to run, refusing one that cannot be run at all.

        Gate runs are NOT serialised here. They were, by one host-wide lock file, because
        the build tool rebuilt its shared environment before every target and two gates at
        once tore it down under each other. bmk >= 3.17.0 guards that environment itself -
        every bmk holds a shared lock on it for its lifetime and the upgrade takes the same
        lock exclusively - so only the provisioning waits, not the whole gate. Serialising
        here as well would be strictly blunter: it is what made ``--parallel`` bound the
        agent nodes but not the gates.

        Args:
            command: The gate command; the default is :data:`DEFAULT_GATE_COMMAND`.

        Raises:
            ValueError: ``command`` is empty. There is no runnable empty argv, so a gate
                built from one could only fail once a node reached it, at which point the
                run has already spent whatever produced the change being gated.
        """
        if not tuple(command):
            raise ValueError("a gate command cannot be empty: there is no argv to run")
        self._command = tuple(command)

    @property
    def command(self) -> tuple[str, ...]:
        """The argv this gate runs.

        The coordinator records this as the gate node's ``argv`` rather than being told one
        separately: a caller that states the command a second time can state it WRONG, and
        nothing downstream can tell - the journal, the node's ``input.json`` and its brief
        would all name a command the machine never ran.
        """
        return self._command

    def run(self, worktree: Path, log: Path) -> int:
        """Run the gate and return its exit code.

        The subprocess gets an EXPLICIT environment, :func:`gate_env` of this process's
        own - never the coordinator's whole environment by inheritance. Which side the
        coordinator was started from (an operator's interactive shell under
        ``--foreground``, or a background scope) then stops changing what the gate sees.

        The log opens with a header naming what the allowlist WITHHELD (names only, never
        values). A gate that fails because a variable it needed was filtered out fails
        inside the project's own tooling, with a message about that project - so the
        allowlist is the last thing anyone suspects. Reading the failing gate log is
        already the first move; the evidence belongs in the same file.

        Args:
            worktree: The working tree to run the gate in.
            log: File the header, then the combined stdout and stderr, are written to,
                owner-only.

        Returns:
            The gate's exit code; ``0`` means the change passed.

        """
        # Suppression below: the gate command comes from the composition root, never user text.
        proc = subprocess.run(  # nosec B603  # noqa: S603
            list(self._command),
            cwd=worktree,
            env=gate_env(os.environ),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        _write_owner_only(log, _log_header(os.environ) + proc.stdout + proc.stderr)
        return proc.returncode


def _log_header(environ: Mapping[str, str]) -> str:
    """Return the gate log's first lines: the command's environment, accounted for.

    Args:
        environ: The environment the gate was filtered from.

    Returns:
        Two lines - what the gate received, and what was withheld from it - then a blank
        line, so the gate's own output starts on a line of its own.
    """
    kept = ", ".join(sorted(gate_env(environ)))
    dropped = ", ".join(withheld_names(environ)) or "(nothing)"
    return f"agentdag gate environment, passed: {kept}\nagentdag gate environment, withheld: {dropped}\n\n"


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
