"""Production wiring for the kernel coordinator: journal, lock, clock, executor, gate, scope.

:class:`~agentdag.application.kernel.ports.KernelWiring` types its fields as the PORTS, so
assigning the concrete adapters here is itself the conformance check: if an adapter drifts
from its protocol, this module stops type-checking - the same reasoning
:mod:`agentdag.composition.graph_a` documents for its own wiring.

Contents:
    * :func:`wire_kernel` - build the production wiring for one CLI invocation.
    * :func:`manager_state_is_live` - the pure scope-selection decision, public so
      ``tests/test_kernel_scope.py`` can pin its table directly.
    * :func:`build_op_registry` - the M6 op registry (Task 30): every op with a real body
      wired to its :class:`~agentdag.application.kernel.context.Coordinator` primitive,
      ``plan``/``judge`` registered as the one not-yet-wired placeholder this task carries,
      and ``apply`` deliberately never registered at all.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - probing the user systemd manager IS this module's job
import sys
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

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
from ..application.kernel.registry import OpRegistry, OpSpec
from ..domain.kernel_errors import KernelError
from ..domain.models import ApproveOption, ApprovePayload, NodeOutcome, NodeStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ..adapters.kernel.executor_claude import CredentialSource
    from ..application.kernel.notify import Notifier
    from ..application.kernel.ports import Scope
    from ..application.kernel.registry import Body, PlanContext
    from ..domain.models import Decision, ResultRecord
    from ..domain.plan import Entry

__all__ = ["build_op_registry", "manager_state_is_live", "wire_kernel"]

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


# --- The M6 op registry (Task 30) -----------------------------------------------------------
#
# Every op with a real body below is wired straight to the matching Coordinator primitive
# (work/gate/scan/reduce/approve): those methods already call `_dispatch` internally and
# return a ResultRecord (Decision for `approve`), so a build() closure here is a thin,
# zero-argument thunk over an already-bound call to one of them - see
# `application/kernel/registry.py`'s own docstring on `Body` for why that is a DIFFERENT
# shape from `dispatch.Body` (`Callable[[Path], Awaitable[NodeOutcome]]`) and deliberately so.
# Building the registry touches no live Coordinator: every closure below takes one only
# through the `PlanContext` its RETURNED body reads when finally invoked (a later task).


class _WorkArgs(BaseModel):
    """``work``'s own args: nothing beyond the entry's spec and brief carry already."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _GateMakeTestArgs(BaseModel):
    """``gate:make-test``'s own args: the ``make test`` argv is fixed by the op, not an arg."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _ScanArgs(BaseModel):
    """``scan``'s own args: which entry's write set it watches."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    watched: str


class _ReduceCountArgs(BaseModel):
    """``reduce:count``'s own args: none - it counts every dispatched record's own status."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _ApproveArgs(BaseModel):
    """``approve``'s own args: everything an :class:`~agentdag.domain.models.ApprovePayload`
    needs besides ``node_id``/``workflow``/``run_id``, which the build below fills in from
    the entry and the coordinator rather than asking a plan to restate them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    artefact_refs: list[str] = Field(default_factory=list)
    options: list[ApproveOption]
    default: str
    decide_by: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


class _NotYetWiredArgs(BaseModel):
    """``plan``/``judge``'s own args: none - neither body reads them (M6 component 3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _build_work(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``work``'s body: dispatch ``entry`` through :meth:`Coordinator.work`."""

    async def body() -> ResultRecord:
        return await ctx.co.work(entry.spec, brief=entry.brief, cwd=ctx.cwd)

    return body


def _build_gate_make_test(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``gate:make-test``'s body: dispatch ``entry`` through :meth:`Coordinator.gate`."""

    async def body() -> ResultRecord:
        return await ctx.co.gate(entry.spec, argv=("make", "test"), cwd=ctx.cwd)

    return body


def _build_scan(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``scan``'s body: dispatch ``entry`` through :meth:`Coordinator.scan`.

    ``before`` is read at DISPATCH time here, not from before the watched node ran: a real
    scan needs a snapshot taken BEFORE its watched node's own dispatch, and orchestrating
    that timing across two separate op dispatches is outside what a single, args-only
    ``build`` can do - a known gap for whichever later task first wires this into a real
    multi-entry dispatch loop (Task 31 is exactly where this would surface).
    """
    args = _ScanArgs.model_validate(dict(entry.args))

    async def body() -> ResultRecord:
        before = ctx.co.snapshot()
        return await ctx.co.scan(entry.spec, watched=args.watched, before=before, write_set=entry.spec.write_set)

    return body


def _build_reduce_count(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``reduce:count``'s body: fold the run's records into a count of ``status == passed``."""

    def fold() -> NodeOutcome:
        count = sum(1 for record in ctx.co.dispatcher.records.values() if record.key_facts.get("status") == "passed")
        return NodeOutcome(
            status=NodeStatus.DONE,
            key_facts={"count": count},
            typed_fields=["count"],
            executor_used="code",
            model_used="-",
            effort_used="-",
        )

    async def body() -> ResultRecord:
        return await ctx.co.reduce(entry.spec, fold=fold)

    return body


def _build_approve(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``approve``'s body: dispatch ``entry`` through :meth:`Coordinator.approve`."""
    args = _ApproveArgs.model_validate(dict(entry.args))
    payload = ApprovePayload(
        text=args.text,
        node_id=entry.spec.node_id,
        artefact_refs=args.artefact_refs,
        options=args.options,
        default=args.default,
        decide_by=args.decide_by,
        workflow=ctx.co.workflow,
        run_id=ctx.co.run_id,
    )

    async def body() -> Decision:
        return await ctx.co.approve(entry.spec, payload=payload)

    return body


def _build_not_yet_wired(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``plan``/``judge``'s body: the one placeholder this task carries (M6 component 3).

    Named so it cannot pass as a working mechanism: :func:`validate_plan` can accept a plan
    that names ``plan`` or ``judge`` today (Task 31 needs exactly that, to find out what
    graph A needs from this registry), but actually invoking either body raises.
    """
    del entry, ctx

    async def body() -> ResultRecord:
        raise KernelError("not yet wired: M6 component 3")

    return body


def build_op_registry() -> OpRegistry:
    """Build the op registry the M6 kernel validates plans against (Task 30).

    Every op with a real body today is wired straight to the matching
    :class:`~agentdag.application.kernel.context.Coordinator` primitive; ``plan`` and
    ``judge`` are registered with a body that raises - the ONE placeholder this task
    carries (M6 component 3) - so a plan naming them validates by NAME today without
    pretending either is dispatchable yet. ``apply`` is deliberately never registered:
    design 2.4 says it is never planner-emitted, so a plan naming it is refused by
    absence, the same as any other name nothing registered.

    Building this registry touches no live :class:`~agentdag.application.kernel.context.
    Coordinator` - every ``build`` closure above takes one only when its returned
    :data:`~agentdag.application.kernel.registry.Body` is actually invoked (a later
    task), never at registration time, which is what makes this a plain, no-argument
    function rather than something that needs a running coordinator to call.

    Returns:
        The registry, with ``work``, ``gate:make-test``, ``scan``, ``reduce:count``,
        ``approve``, ``plan`` and ``judge`` registered.
    """
    registry = OpRegistry()
    registry.register(
        OpSpec(
            name="work",
            args_model=_WorkArgs,
            output_contract=frozenset({"status", "artifact_ref"}),
            can_change_state=True,
            build=_build_work,
        )
    )
    registry.register(
        OpSpec(
            name="gate:make-test",
            args_model=_GateMakeTestArgs,
            output_contract=frozenset({"rc"}),
            can_change_state=False,
            build=_build_gate_make_test,
        )
    )
    registry.register(
        OpSpec(
            name="scan",
            args_model=_ScanArgs,
            output_contract=frozenset({"stray"}),
            can_change_state=True,
            build=_build_scan,
        )
    )
    registry.register(
        OpSpec(
            name="reduce:count",
            args_model=_ReduceCountArgs,
            output_contract=frozenset({"count"}),
            can_change_state=True,
            build=_build_reduce_count,
        )
    )
    registry.register(
        OpSpec(
            name="approve",
            args_model=_ApproveArgs,
            output_contract=frozenset({"decision"}),
            can_change_state=True,
            build=_build_approve,
        )
    )
    registry.register(
        OpSpec(
            name="plan",
            args_model=_NotYetWiredArgs,
            output_contract=frozenset(),
            can_change_state=True,
            build=_build_not_yet_wired,
        )
    )
    registry.register(
        OpSpec(
            name="judge",
            args_model=_NotYetWiredArgs,
            output_contract=frozenset({"verdict"}),
            can_change_state=True,
            build=_build_not_yet_wired,
        )
    )
    return registry
