"""Production wiring for the kernel coordinator: journal, lock, clock, executor, gate, scope.

:class:`~agentdag.application.kernel.ports.KernelWiring` types its fields as the PORTS, so
assigning the concrete adapters here is itself the conformance check: if an adapter drifts
from its protocol, this module stops type-checking - the same reasoning
:mod:`agentdag.composition.graph_a` documents for its own wiring.

Contents:
    * :func:`wire_kernel` - build the production wiring for one CLI invocation.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - probing the user systemd manager IS this module's job
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ..adapters.graph_a.gate_make import MakeTestGate
from ..adapters.graph_a.git_cli import GitCli
from ..adapters.kernel.clock_utc import UtcClock
from ..adapters.kernel.executor_claude import ClaudeExecutor
from ..adapters.kernel.isolation_scan import IsolationScanner
from ..adapters.kernel.journal_jsonl import JsonlJournal
from ..adapters.kernel.lock_file import FileRunLock
from ..adapters.kernel.policy_yaml import load_policy
from ..adapters.kernel.scope_none import NoScope
from ..adapters.kernel.scope_systemd import SystemdScope
from ..application.kernel.ports import KernelWiring

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..adapters.kernel.executor_claude import CredentialSource
    from ..application.kernel.ports import Scope

__all__ = ["wire_kernel"]

_GATE_LOCK = Path(tempfile.gettempdir()) / "agentdag-bmk-tool-env.lock"
"""Host-wide lock serialising the gate: the same shared bmk tool environment
``adapters.cli.commands.graph_a.DEFAULT_LOCK`` guards for the M1 baseline - one physical
resource, so the kernel's own gate nodes wait behind the SAME lock file rather than a
second one that could race it."""

_SYSTEM_RUNNING_STATES = frozenset({0, 1})
"""``systemctl --user is-system-running`` exit codes that mean the manager itself answers:
0 (``running``) or 1 (``degraded`` - one failed unit, not a dead manager)."""


def wire_kernel(
    *,
    runs: Path,
    policy_path: Path,
    credential: CredentialSource,
    parallel: int,
    max_turns: int,
    deny_bash: Sequence[str],
) -> KernelWiring:
    """Build the production kernel wiring for one CLI invocation of ``agentdag run``.

    Args:
        runs: The directory holding every run; carried through unchanged as
            :attr:`~agentdag.application.kernel.ports.KernelWiring.runs_dir`.
        policy_path: The tier policy YAML to load.
        credential: Where the Claude executor's per-node login comes from - resolved by
            the CLI (:class:`~agentdag.adapters.kernel.executor_claude.OAuthTokenFile` or
            :class:`~agentdag.adapters.kernel.executor_claude.CredentialCopy`), never by
            this function: choosing it needs to check whether a keyfile PATH exists, and
            reporting that choice is the CLI's job, not the composition root's.
        parallel: How many map branches a launch may run at once.
        max_turns: The SDK turn ceiling every node dispatch runs under.
        deny_bash: The Bash command denylist every node's PreToolUse hook enforces.

    Returns:
        The wiring for one launch (or relaunch) of the coordinator.
    """
    return KernelWiring(
        journal_factory=JsonlJournal,
        lock=FileRunLock(),
        clock=UtcClock(),
        executors={"claude": ClaudeExecutor(credentials=credential, deny_bash=tuple(deny_bash))},
        gate_port=MakeTestGate(lock=_GATE_LOCK),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=load_policy(policy_path, max_turns=max_turns, deny_bash=deny_bash),
        scope=_choose_scope(),
        runs_dir=runs,
        parallel=parallel,
    )


def _choose_scope() -> Scope:
    """Pick :class:`SystemdScope` on Linux with a live user manager, else :class:`NoScope`.

    Never falls back silently past a genuine failure: this only ever chooses between the
    two scopes by PROBING whether ``systemd-run``/the user manager are available at all,
    not by trying ``SystemdScope`` and catching an error from it - a real failure to start
    the coordinator under a scope that SHOULD work is a STOP condition (Task 17), reported
    as-is rather than masked by a quiet fallback to ``NoScope``.
    """
    if sys.platform == "linux" and shutil.which("systemd-run") and _user_manager_is_up():
        return SystemdScope()
    return NoScope()


def _user_manager_is_up() -> bool:
    """Return whether ``systemctl --user is-system-running`` reports the manager alive.

    Its exit code is 0 for ``running`` and 1 for ``degraded`` (one failed unit, not a dead
    manager); anything else - not running at all, or the command missing entirely - means
    no user scope can be created here.
    """
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return False
    result = subprocess.run(  # nosec B603  # noqa: S603 - a resolved executable and a fixed argument list, never a shell string
        [systemctl, "--user", "is-system-running"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode in _SYSTEM_RUNNING_STATES
