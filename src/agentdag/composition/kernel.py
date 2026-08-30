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
      ``plan`` registered with a GUARD body the scheduler must never reach (Task 32), and
      ``apply`` and ``judge`` deliberately never registered at all.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - probing the user systemd manager IS this module's job
import sys
from functools import partial
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
from ..domain.condition import referenceable_view
from ..domain.kernel_errors import KernelError
from ..domain.models import ApproveOption, ApprovePayload, NodeOutcome, NodeStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
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
#
# Every registration carries a `# state:` line DIRECTLY above its `can_change_state=`, giving
# the one-line reason that flag has the value it has, read off this op's own body. The flag is
# set by what the body DOES - True when a record from this op can tell "this work finished"
# from "this work never started", False when the op only OBSERVES and its reading is the same
# before the work and after it - never by the op's NAME; that every `gate:*` comes out False is
# a consequence of the test, not the test itself (see `registry.OpSpec.can_change_state`).
# `tests/test_kernel_registry.py` parses those lines back out of this file and fails on a
# registration that carries none, so an unexamined flag is an omission rather than a default.


_WORK_CONTRACT = frozenset(
    {
        # Every `key_facts` name a `work` dispatch can come back with, read off the four
        # outcome constructors in adapters/kernel/executor_claude.py - a `work` node goes
        # through Coordinator.work to the wired executor, so those ARE its body:
        "turns",  # outcome_from_usage (:493), the ordinary path
        "first_turn_input_tokens",  # all four (:493, :1182, :1296, :1400)
        "context_at_handover",  # _Handover's outcome (:1180)
        "handover_at_tokens",  # (:1181)
        "grace_used",  # (:1183)
        "grace_expired",  # (:1184)
        "cap_hit",  # the token-cap stop (:1296)
        "deadline_hit",  # the node-deadline stop (:1400)
    }
)
"""What a ``work`` record can carry, as a CEILING on what a condition may name.

The whole union rather than the ordinary path's two names, because that is what
:attr:`~agentdag.application.kernel.registry.OpSpec.output_contract` is: the set a
``FieldRef`` is checked against. A ceiling that omitted ``cap_hit`` would refuse a plan
branching on a real, emitted fact. It is NOT a promise that any one dispatch carries all of
them - a record carries whichever path produced it.

``status`` is deliberately absent: it is a TOP-LEVEL record field, not a ``key_fact``, and
it is referenceable on EVERY op through
:data:`~agentdag.domain.condition.RESERVED_TOP_LEVEL_FIELDS`. ``artifact_ref`` is absent
because no such name exists anywhere in this codebase - the top-level field is spelled
``artefact_refs``, and it is not a ``key_fact`` either."""


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


class _PlanArgs(BaseModel):
    """``plan``'s own args: the nested sub-goal the planner is dispatched for (design 3.3).

    ``goal`` is REQUIRED, and Task 33 is what made it a field rather than only a promise in
    this docstring: :func:`~agentdag.application.kernel.execute.execute_plan` recurses on a
    ``plan`` entry by calling
    :func:`~agentdag.application.kernel.planner.dispatch_planner`, whose ``goal`` argument
    has nowhere else to come from - ``Entry.brief`` is the node's own instructions and
    ``Plan.goal`` belongs to the plan that CONTAINS this entry, not to the sub-plan it asks
    for. Required rather than defaulted, so a plan entry with no sub-goal is refused at
    plan-accept time instead of dispatching a planner with nothing to plan.

    The class was named ``_NotYetWiredArgs`` until here, after the placeholder body Task 32
    retired; it never described the args.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str


_ArgsT = TypeVar("_ArgsT", bound=BaseModel)


def _parse_args(model: type[_ArgsT], entry: Entry) -> _ArgsT:
    """Validate ``entry.args`` against ``model``, raising the kernel's own error type.

    A build re-validates because it needs the PARSED values to close over, and
    :func:`~agentdag.application.kernel.plan_validate.validate_plan` having already checked
    the same args does not make this a formality - a body can be built from an entry that
    never went through a plan. What must not happen is a raw pydantic ``ValidationError``
    escaping here: every other failure a caller of ``build`` can hit is a
    :class:`~agentdag.domain.kernel_errors.KernelError`, so an ``except KernelError`` around
    building a node would let exactly this one through.

    Args:
        model: The op's own args model.
        entry: The plan entry naming this op.

    Returns:
        The parsed args.

    Raises:
        KernelError: ``entry.args`` do not validate against ``model``.
    """
    try:
        return model.model_validate(dict(entry.args))
    except ValidationError as exc:
        raise KernelError(f"entry {entry.spec.node_id!r} args invalid for op {entry.op!r}: {exc}") from exc


def _build_work(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``work``'s body: dispatch ``entry`` through :meth:`Coordinator.work`.

    The stop predicate is bound to THIS entry's node id here, so the executor is handed a
    plain zero-argument callable and never learns what a ``StopScope`` is. ``None`` when the
    context carries no scope, which is a dispatch belonging to no plan.
    """

    async def body() -> ResultRecord:
        return await ctx.co.work(entry.spec, brief=entry.brief, cwd=ctx.cwd, is_stopping=_stop_predicate(entry, ctx))

    return body


def _stop_predicate(entry: Entry, ctx: PlanContext) -> Callable[[], bool] | None:
    """Bind this entry's node id into the scope's predicate, or None outside a plan.

    A ``partial`` rather than a lambda: the executor's parameter is a plain
    ``Callable[[], bool]``, and a bound method with its argument already applied says what
    this is - the scope's own answer about one node - where a lambda would only say that
    something returns a bool.
    """
    scope = ctx.stopping
    return None if scope is None else partial(scope.is_stopping, entry.spec.node_id)


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
    args = _parse_args(_ScanArgs, entry)

    async def body() -> ResultRecord:
        before = ctx.co.snapshot()
        return await ctx.co.scan(entry.spec, watched=args.watched, before=before, write_set=entry.spec.write_set)

    return body


def _build_reduce_count(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``reduce:count``'s body: fold the run's records into a count of the ones that passed.

    "Passed" is :attr:`~agentdag.domain.models.NodeStatus.DONE`: that is what graph A's own
    branch verdict reads (``application/workflows/graph_a.py``: ``passed = gate.status is
    NodeStatus.DONE and scan.status is NodeStatus.DONE``). ``"passed"`` is not a member of
    :class:`~agentdag.domain.models.NodeStatus` at all, and ``status`` is a TOP-LEVEL record
    field, never a ``key_fact`` - a fold reading ``key_facts["status"]`` counted 0 forever.

    The count goes through :func:`~agentdag.domain.condition.referenceable_view` and compares
    against the enum's own ``.value``, so it reads ``status`` exactly the way a
    :class:`~agentdag.domain.condition.Compare` in a plan's ``done_when`` does - one merge
    rule and one comparison form, not two that can drift apart.
    """
    passed = NodeStatus.DONE.value

    def fold() -> NodeOutcome:
        count = sum(
            1 for record in ctx.co.dispatcher.records.values() if referenceable_view(record)["status"] == passed
        )
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
    args = _parse_args(_ApproveArgs, entry)
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


def _build_plan_entry_guard(entry: Entry, ctx: PlanContext) -> Body:
    """Build ``plan``'s body, which is a guard: reaching it means the scheduler has a bug.

    ``plan`` is registered so :func:`validate_plan` accepts a plan whose entry names a nested
    sub-goal - that recursion is the centre of the design - but it is NOT dispatched through
    this registry. Design section 3.3's scheduler special-cases it:

        if entry.op == "plan":  dispatch the planner, then execute(sub)   # recursion
        else:                   dispatch(registry[entry.op], entry)

    So the body exists only to be never called, and it raises to say so. Task 32 first
    intended to give it a real body calling ``dispatch_planner``; that would have been half a
    mechanism, planning a sub-plan and discarding it, because nothing here can run the result.
    The execute loop that CAN is Task 33, and it reaches the planner directly.

    ``judge`` shared this builder until Checkpoint B (2026-08-29) unregistered it. The two
    were never equivalent: ``plan`` must stay registered for a nested sub-goal to validate at
    all, while ``judge`` had no body in prospect, so leaving IT registered meant a plan could
    be accepted and then die at dispatch, after spend.
    """
    del ctx

    async def body() -> ResultRecord:
        raise KernelError(
            f"op 'plan' (entry {entry.spec.node_id!r}) was dispatched through the registry; "
            "the execute loop must special-case a plan entry into its own recursion (design 3.3)"
        )

    return body


def build_op_registry() -> OpRegistry:
    """Build the op registry the M6 kernel validates plans against (Task 30).

    Every op with a real body today is wired straight to the matching
    :class:`~agentdag.application.kernel.context.Coordinator` primitive; ``plan`` is
    registered with a body that raises - the ONE placeholder left (M6 component 3) - so a
    plan naming it validates by NAME today without pretending it is dispatchable yet.
    ``apply`` and ``judge`` are deliberately never registered: design 2.4 says ``apply`` is
    never planner-emitted, and Checkpoint B (2026-08-29) took ``judge`` out until something
    can actually run it. A plan naming either is refused by absence, the same as any other
    name nothing registered.

    Building this registry touches no live :class:`~agentdag.application.kernel.context.
    Coordinator` - every ``build`` closure above takes one only when its returned
    :data:`~agentdag.application.kernel.registry.Body` is actually invoked (a later
    task), never at registration time, which is what makes this a plain, no-argument
    function rather than something that needs a running coordinator to call.

    Returns:
        The registry, with ``work``, ``gate:make-test``, ``scan``, ``reduce:count``,
        ``approve`` and ``plan`` registered.
    """
    registry = OpRegistry()
    registry.register(
        OpSpec(
            name="work",
            args_model=_WorkArgs,
            output_contract=_WORK_CONTRACT,
            # state: True - the record IS work an executor did; only running it makes one (context.py:198)
            can_change_state=True,
            build=_build_work,
        )
    )
    registry.register(
        OpSpec(
            name="gate:make-test",
            args_model=_GateMakeTestArgs,
            output_contract=frozenset({"rc"}),  # application/kernel/context.py:499
            # state: False - the gate only READS a check that was green before the work too (context.py:489)
            can_change_state=False,
            build=_build_gate_make_test,
        )
    )
    registry.register(
        OpSpec(
            name="scan",
            args_model=_ScanArgs,
            output_contract=frozenset({"stray"}),  # application/kernel/context.py:584
            # Set from what the body does, not from the brief's `gate:` NAME PREFIX rule, which
            # this op's name does not match: `scan.stray == 0` alone would otherwise be the same
            # never-started-reads-as-finished loophole decision 4 closes for gates.
            # state: False - a manifest diff only OBSERVES; a clean scan reads alike, run or not (context.py:580)
            can_change_state=False,
            build=_build_scan,
        )
    )
    registry.register(
        OpSpec(
            name="reduce:count",
            args_model=_ReduceCountArgs,
            output_contract=frozenset({"count"}),  # _build_reduce_count's own fold, above
            # state: True - the fold above counts 0 with nothing dispatched and N once N passed (kernel.py:360)
            can_change_state=True,
            build=_build_reduce_count,
        )
    )
    registry.register(
        OpSpec(
            name="approve",
            args_model=_ApproveArgs,
            output_contract=frozenset({"decision"}),  # application/kernel/context.py:798
            # state: True - a Decision exists only because a person answered THIS payload (context.py:787)
            can_change_state=True,
            build=_build_approve,
        )
    )
    registry.register(
        OpSpec(
            name="plan",
            args_model=_PlanArgs,
            # Empty because the body raises and emits nothing - never a guessed field. A
            # condition can still name a plan entry's `status`, which is reserved.
            output_contract=frozenset(),
            # OPEN, and do not build on this flag until it is settled. A sub-plan could be all
            # gates, and NOTHING catches that: decision 4's rule runs under `if is_root`
            # (plan_validate.py:114), so a nested plan is never checked against it. A root
            # done_when naming a plan entry therefore passes the rule while its subtree may
            # change no state - the loophole decision 4 exists to close, reached one level down.
            # Not exploitable yet: a plan entry is not executed until the loop that recurses on
            # it exists. An earlier version of this comment claimed the sub-plan's own
            # validation caught it; that was false and is corrected here.
            # state: True - a plan entry stands for the subtree it expands into, which runs the state-changing work
            can_change_state=True,
            build=_build_plan_entry_guard,
        )
    )
    # `judge` is deliberately NOT registered, like `apply` (Checkpoint B, user 2026-08-29).
    # A registered-but-raising op validates by NAME and dies at dispatch, so a plan naming it
    # is accepted and the run fails mid-flight, after spend; absence refuses it at plan-accept
    # time instead. It also avoids shipping an unverified `can_change_state`: there is no body,
    # so no emitter has been read, and decision 4's rule keys on precisely that flag. The task
    # that builds the judge sets the flag by reading the emitter it writes.
    return registry
