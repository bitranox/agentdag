"""Production wiring for the kernel coordinator: journal, lock, clock, executor, gate, scope.

:class:`~agentdag.application.kernel.ports.KernelWiring` types its fields as the PORTS, so
assigning the concrete adapters here is itself the conformance check: if an adapter drifts
from its protocol, this module stops type-checking - the same reasoning
:mod:`agentdag.composition.graph_a` documents for its own wiring.

Contents:
    * :func:`wire_kernel` - build the production wiring for one CLI invocation.
    * :func:`manager_state_is_live` - the pure scope-selection decision, public so
      ``tests/test_kernel_scope.py`` can pin its table directly.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - probing the user systemd manager IS this module's job
import sys
from typing import TYPE_CHECKING

from ..adapters.graph_a.gate_make import MakeTestGate
from ..adapters.graph_a.git_cli import GitCli
from ..adapters.kernel.clock_utc import UtcClock
from ..adapters.kernel.credential_probe import ApiCredentialProbe
from ..adapters.kernel.executor_claude import ClaudeExecutor
from ..adapters.kernel.isolation_scan import IsolationScanner
from ..adapters.kernel.journal_jsonl import JsonlJournal
from ..adapters.kernel.lock_file import FileRunLock
from ..adapters.kernel.policy_yaml import load_policy
from ..adapters.kernel.sandbox_none import NoSandbox
from ..adapters.kernel.scope_none import NoScope
from ..adapters.kernel.scope_systemd import SystemdScope
from ..application.kernel.ports import KernelWiring

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ..adapters.kernel.executor_claude import CredentialSource
    from ..application.kernel.notify import Notifier
    from ..application.kernel.ports import Scope

__all__ = ["manager_state_is_live", "wire_kernel"]

_LIVE_MANAGER_STATES = frozenset({"running", "degraded"})
"""``systemctl --user is-system-running``'s own STDOUT values that mean a live manager:
``running`` (exit 0) or ``degraded`` (exit 1 - one failed unit, not a dead manager). The
exit code alone is not the signal: an EMPTY stdout can occur at either code (no user
session at all), and that is what actually means no scope can be created here - see
:func:`manager_state_is_live`."""


def wire_kernel(
    *,
    policy_path: Path,
    credential: CredentialSource,
    parallel: int,
    max_turns: int,
    deny_bash: Sequence[str],
    notifier: Notifier,
) -> KernelWiring:
    """Build the production kernel wiring for one CLI invocation of ``agentdag run``.

    Args:
        policy_path: The tier policy YAML to load.
        credential: Where the Claude executor's per-node login comes from - resolved by
            the CLI (:class:`~agentdag.adapters.kernel.executor_claude.OAuthTokenFile` or
            :class:`~agentdag.adapters.kernel.executor_claude.CredentialCopy`), never by
            this function: choosing it needs to check whether a keyfile PATH exists, and
            reporting that choice is the CLI's job, not the composition root's.
        parallel: How many map branches a launch may run at once.
        max_turns: The SDK turn ceiling every node dispatch runs under.
        deny_bash: The Bash command denylist every node's PreToolUse hook enforces.
        notifier: Where this launch's run events go - resolved by the CLI, which is
            the layer that has both the loaded config naming the sink and the email
            adapter the mail sink sends through; this function takes neither.

    Returns:
        The wiring for one launch (or relaunch) of the coordinator.
    """
    clock = UtcClock()
    return KernelWiring(
        journal_factory=JsonlJournal,
        lock=FileRunLock(),
        clock=clock,
        executors={
            "claude": ClaudeExecutor(
                credentials=credential,
                deny_bash=tuple(deny_bash),
                clock=clock,
                # Reads the token from the SAME credential source the dispatch used, so the
                # probe asks about exactly the credential that was refused - a probe pointed
                # at any other one answers a question nobody asked.
                credential_probe=ApiCredentialProbe(read_token=credential.bearer_token),
            )
        },
        gate_port=MakeTestGate(),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=load_policy(policy_path, max_turns=max_turns, deny_bash=deny_bash),
        scope=_choose_scope(),
        sandbox=NoSandbox(),
        notifier=notifier,
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

    The command missing entirely means no: there is nothing to run the probe with.
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
    return manager_state_is_live(result.stdout)


def manager_state_is_live(stdout: str) -> bool:
    """Decide from ``systemctl --user is-system-running``'s own STDOUT whether a scope can start.

    Keyed on stdout, not the exit code: the command exits 0 for ``running`` and 1 for
    ``degraded`` (one failed unit, not a dead manager) - both real, live answers - but an
    EMPTY stdout (no user session at all) can occur at EITHER exit code, and that is what
    actually means no scope can be created here. A pure function so the decision table is
    directly testable without spawning ``systemctl`` (see ``tests/test_kernel_scope.py``).

    Args:
        stdout: ``is-system-running``'s own stdout, exactly as captured (not yet stripped).

    Returns:
        Whether ``stdout`` names a live manager state.

    Example:
        >>> manager_state_is_live("running\\n")
        True
        >>> manager_state_is_live("degraded\\n")
        True
        >>> manager_state_is_live("")
        False
    """
    return stdout.strip() in _LIVE_MANAGER_STATES
