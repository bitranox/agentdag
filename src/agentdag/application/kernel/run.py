"""Start or resume one run: the lock, the journal's bookend lines, the state file (design 3.4).

One launch of a coordinator, whether it is a run's first or its fifth. Everything that
distinguishes the two is read from the journal, never passed in: an empty journal makes
this a start (``run_started``), a non-empty one makes it a resume (``resume``), and the
replay index folded from it decides which nodes the program's re-execution actually
dispatches.

Three exits, and the fourth that is not an exit at all:

* the program returns   -> a ``run_summary`` line, state ``done``, cursor cleared;
* it raises ``Suspended`` -> state ``suspended``, cursor at the approve node and
  ``cursor_payload_hash`` at the payload it is waiting on (a decision is recorded per
  (node id, payload hash), so the node id alone does not say what to answer), no summary;
* it raises anything else -> state ``failed``, and the exception propagates;
* the PROCESS dies (``SystemExit``, ``KeyboardInterrupt``) -> nothing is written, so the
  state on disk stays ``running`` with a ``started`` line that has no ``result``. That IS
  the crash window, and it is what the next launch re-dispatches. The lock is still
  released, because releasing it is the only thing that has to happen either way.

The first three exits also EMIT a run event, because each is a thing an operator who is
not watching needs told. The fourth cannot - there is nobody left to emit - so
:mod:`~agentdag.application.kernel.crash` observes it from outside instead.

At ``parallel > 1`` the crash window is not necessarily ONE key: more than one map
branch can be mid-flight at once, so a crash can leave several ``started``-without-
``result`` keys at the same time - e.g. one branch's work node and a sibling's gate,
each on its own worktree. The next launch re-dispatches every one of them, in whatever
order the map schedules them. That is what concurrent branches sharing one crash looks
like, not a defect: each key still re-dispatches exactly the node whose result never
landed, however many there are.

Contents:
    * :class:`RunOutcome` - what one launch reports back to its caller.
    * :func:`run_coordinator` - run one launch to one of the three exits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from ...domain.journal import ResumeLine, RunStartedLine
from ...domain.kernel_errors import RunRefused, Suspended
from ...domain.models import ApprovePayload, RunState, RunStatus, SuspendReason
from .approve import suspend_payload_rel
from .context import Coordinator
from .dispatch import Dispatcher
from .notify import RunEvent, emit_best_effort
from .ports import stamp
from .summary import append_run_summary
from .workflow_check import assert_deterministic

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ...domain.models import LockHolder
    from ..graph_a_ports import GatePort, GitPort
    from ..workflows import WorkflowDef
    from .notify import Notifier
    from .ports import Clock, Executor, IsolationScanner, Journal, Policy, RunDir, RunLock
    from .sandbox import Sandbox

__all__ = ["RunOutcome", "run_coordinator"]

_ResumeReason = Literal["decision", "crash", "restart", "manual", "retry"]
_RESUME_REASONS: tuple[_ResumeReason, ...] = ("decision", "crash", "restart", "manual", "retry")


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What one launch of a coordinator reports back.

    Attributes:
        status: The run's status as this launch left it on disk.
        suspended_node: The approve node the run is waiting on, or ``None``.
        dispatched_keys: Every key this launch put through the dispatcher, served keys
            included - the replay-purity oracle: a launch that serves everything must
            produce exactly the journal's own ``started`` keys, as a MULTISET. The order
            is guaranteed only at ``parallel == 1``; above it the map's branches
            interleave, so two launches may emit the same keys in a different order.
    """

    status: RunStatus
    suspended_node: str | None
    dispatched_keys: list[str]


async def run_coordinator(
    *,
    run_dir: RunDir,
    journal: Journal,
    clock: Clock,
    lock: RunLock,
    holder: LockHolder,
    workflow: WorkflowDef,
    args: BaseModel,
    executors: Mapping[str, Executor],
    gate_port: GatePort,
    git: GitPort,
    scanner: IsolationScanner,
    policy: Policy,
    sandbox: Sandbox,
    parallel: int,
    by: str,
    token_id: str,
    resume_reason: str | None,
    notifier: Notifier,
) -> RunOutcome:
    """Run one launch of ``workflow`` over ``run_dir`` and report how it ended.

    Args:
        run_dir: The run directory; its name is the run id.
        journal: The run's journal.
        clock: The one seam every timestamp in this launch is read from.
        lock: The run-directory lock; held for the whole launch.
        holder: This process's identity, recorded as the lock's owner. Passed in
            rather than read here because identifying the process is an adapter's job
            (``adapters.kernel.lock_file.current_holder``) and this is the application
            layer.
        workflow: The workflow to run; its module is checked for determinism first.
        args: The workflow's parsed arguments.
        executors: Executor name -> executor, as the policy's rows name them.
        gate_port: The mechanical gate a ``gate`` node runs.
        git: Every git operation the workflow performs.
        scanner: Takes the isolation-root manifest a ``scan`` node compares.
        policy: The tier policy; its version is recorded on the run.
        sandbox: What isolation boundary every node this launch dispatches runs under
            (Task 19); its declaration is stamped onto every dispatched node's record.
        parallel: How many map branches may run at once.
        by: Who launched this; recorded on the opening journal line and, on a first
            start only, as the run's owner.
        token_id: The credential this launch authenticated with, recorded likewise.
        resume_reason: Why this launch is a resume (``decision``, ``crash``, ``retry``,
            ``restart`` or ``manual``); ignored on a run's first start.
        notifier: Where this launch's run events go. Required, not defaulted: every
            other port here is, and a notification channel that can be silently
            forgotten at a call site is one an operator discovers is missing on the run
            they most needed it for. The no-op sink is how an operator says "none".

    Returns:
        The launch's outcome.

    Raises:
        LockHeld: another live coordinator holds ``run_dir``.
        NondeterministicCallError: ``workflow``'s module reaches for the clock or randomness.
        RunRefused: ``args`` is not the workflow's own args model, or ``resume_reason``
            is not one of the known reasons.
        Exception: whatever the program raised, after the run is marked ``failed``.
    """
    token = lock.acquire(run_dir.root, holder)
    try:
        assert_deterministic(workflow.module)
        _refuse_mismatched_args(workflow, args)
        resumed = _bookend(
            journal=journal,
            clock=clock,
            run_id=run_dir.root.name,
            workflow=workflow,
            args=args,
            by=by,
            token_id=token_id,
            policy_version=policy.version,
            resume_reason=resume_reason,
        )
        opened = clock.now()
        dispatcher = Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=clock)
        replay_seconds = (clock.now() - opened).total_seconds() if resumed else None
        co = Coordinator(
            run_id=run_dir.root.name,
            workflow=workflow.name,
            args=args,
            dispatcher=dispatcher,
            run_dir=run_dir,
            clock=clock,
            executors=executors,
            gate_port=gate_port,
            git=git,
            scanner=scanner,
            policy=policy,
            sandbox=sandbox,
            parallel=parallel,
        )
        co.fold_decisions()
        co.fold_retry_grants()
        _write_state(co, status=RunStatus.RUNNING, cursor=None, by=by)
        return await _drive(co, workflow=workflow, args=args, by=by, replay_seconds=replay_seconds, notifier=notifier)
    finally:
        lock.release(token)


async def _drive(
    co: Coordinator,
    *,
    workflow: WorkflowDef,
    args: BaseModel,
    by: str,
    replay_seconds: float | None,
    notifier: Notifier,
) -> RunOutcome:
    """Await the program once and write the state its exit calls for.

    ``except Exception`` deliberately does not cover ``SystemExit``,
    ``KeyboardInterrupt`` or ``CancelledError``: those are the process itself going
    away, and the run must be left ``running`` on disk so the next launch sees a crash
    window rather than a tidy failure.

    Raises:
        Exception: whatever the program raised, re-raised after the failed state is written.
    """
    try:
        await workflow.program(co, args)
    except Suspended as suspended:
        _write_state(
            co,
            status=RunStatus.SUSPENDED,
            cursor=suspended.node_id,
            by=by,
            cursor_payload_hash=suspended.payload_hash,
            suspend_reason=suspended.reason,
        )
        _announce_suspend(co, notifier=notifier, suspended=suspended)
        return RunOutcome(RunStatus.SUSPENDED, suspended.node_id, list(co.dispatcher.dispatched_keys))
    except Exception:
        _write_state(co, status=RunStatus.FAILED, cursor=None, by=by)
        _announce(co, notifier=notifier, status=RunStatus.FAILED)
        raise
    append_run_summary(co, replay_seconds=replay_seconds)
    _write_state(co, status=RunStatus.DONE, cursor=None, by=by)
    _announce(co, notifier=notifier, status=RunStatus.DONE)
    return RunOutcome(RunStatus.DONE, None, list(co.dispatcher.dispatched_keys))


def _announce(co: Coordinator, *, notifier: Notifier, status: RunStatus) -> None:
    """Tell the operator this launch reached ``status``, after the state file says so.

    Ordered after :func:`_write_state` deliberately: somebody who opens the run because
    of the notification must not find it still claiming to be running.

    Args:
        co: The coordinator whose run ended.
        notifier: Where the event goes.
        status: The state reached - ``done`` or ``failed`` here; a suspend has more to
            say and goes through :func:`_announce_suspend`.
    """
    emit_best_effort(
        notifier,
        RunEvent(run_id=co.run_id, workflow=co.workflow, status=status, at=stamp(co.clock)),
    )


def _announce_suspend(co: Coordinator, *, notifier: Notifier, suspended: Suspended) -> None:
    """Tell the operator a run is waiting on them, carrying the question and its deadline.

    The payload is read back from the file the suspend just published rather than from
    the exception, which carries only the hash: what the operator must see is what the
    DECIDER will be shown, and reading the same file both of them read is the only way
    those cannot drift.

    Args:
        co: The coordinator whose run suspended.
        notifier: Where the event goes.
        suspended: The suspend, naming the node and the payload hash it waits on.
    """
    payload = _suspend_payload(co, suspended)
    emit_best_effort(
        notifier,
        RunEvent(
            run_id=co.run_id,
            workflow=co.workflow,
            status=RunStatus.SUSPENDED,
            at=stamp(co.clock),
            suspend_reason=suspended.reason,
            node_id=suspended.node_id,
            summary="" if payload is None else payload.text,
            decide_by=None if payload is None else payload.decide_by,
        ),
    )


def _suspend_payload(co: Coordinator, suspended: Suspended) -> ApprovePayload | None:
    """Read the payload this suspend published, or ``None`` when it cannot be read.

    Never raises: a notification that cannot be fully composed is still worth sending -
    an operator told a run is waiting, without the question, can go and look, while a
    suspend turned into a crash because the payload would not parse helps nobody. The
    payload was written moments ago by this same process, so ``None`` here means
    something is wrong with the run directory, not with the suspend.
    """
    if suspended.payload_hash is None:
        return None
    try:
        text = co.run_dir.read_text(suspend_payload_rel(suspended.node_id, suspended.payload_hash))
        return ApprovePayload.model_validate_json(text)
    except (OSError, ValueError):
        return None


def _refuse_mismatched_args(workflow: WorkflowDef, args: BaseModel) -> None:
    """Refuse arguments that are not this workflow's own, BEFORE anything is written.

    ``WorkflowDef.program`` is typed over ``Any``, so a mismatch type-checks; without
    this the run takes the lock, journals a ``run_started`` line holding the wrong
    argument shape and writes ``state.json`` as ``running``, and only then dies inside
    the program on an attribute that does not exist - leaving a run directory that
    describes a run nobody can resume.

    Raises:
        RunRefused: ``args`` is not an instance of ``workflow.args_model``.
    """
    if not isinstance(args, workflow.args_model):
        raise RunRefused(f"workflow {workflow.name!r} takes {workflow.args_model.__name__}, not {type(args).__name__}")


def _bookend(
    *,
    journal: Journal,
    clock: Clock,
    run_id: str,
    workflow: WorkflowDef,
    args: BaseModel,
    by: str,
    token_id: str,
    policy_version: str,
    resume_reason: str | None,
) -> bool:
    """Append this launch's opening line and report whether it was a RESUME.

    An empty journal is the only thing that makes a launch a start, so a caller cannot
    turn a resume into a second ``run_started`` by forgetting an argument.

    Returns:
        Whether the journal already held lines (i.e. this launch is a resume).
    """
    if journal.lines():
        journal.append(
            ResumeLine(run_id=run_id, reason=_reason(resume_reason), by=by, token_id=token_id, at=stamp(clock))
        )
        return True
    journal.append(
        RunStartedLine(
            run_id=run_id,
            workflow=workflow.name,
            args=args.model_dump(mode="json"),
            by=by,
            token_id=token_id,
            policy_version=policy_version,
            at=stamp(clock),
        )
    )
    return False


def _reason(resume_reason: str | None) -> _ResumeReason:
    """Narrow a caller's resume reason to the ones the journal line accepts.

    Args:
        resume_reason: The reason given, or ``None`` for the unremarkable case.

    Returns:
        The reason, defaulting to ``"manual"``.

    Raises:
        RunRefused: the reason is not one of the known ones.

    Example:
        >>> _reason(None), _reason("crash")
        ('manual', 'crash')
    """
    if resume_reason is None:
        return "manual"
    if resume_reason not in _RESUME_REASONS:
        raise RunRefused(f"resume reason {resume_reason!r} is not one of {list(_RESUME_REASONS)}")
    return resume_reason


def _write_state(
    co: Coordinator,
    *,
    status: RunStatus,
    cursor: str | None,
    by: str,
    cursor_payload_hash: str | None = None,
    suspend_reason: SuspendReason | None = None,
) -> None:
    """Write ``state.json`` for this launch, keeping what only the first start decides.

    ``args`` and ``owner`` are read back from an existing state file rather than
    rewritten: they are the run's identity, and a relaunch by somebody else with a
    re-parsed argument set must not silently redefine what the run IS.
    ``tokens_by_row`` is the opposite - it is OVERWRITTEN with the coordinator's own
    totals, which are rebuilt from zero on every launch by charging every record the
    launch touched, served ones included. Adding them to what the file already held
    would count the same records once per launch.

    ``cursor_payload_hash`` defaults to ``None`` and is passed only on the suspend
    path, so any other exit CLEARS it: a stale hash would name a payload the run is no
    longer waiting on, and a decider reading it would answer the wrong question.
    ``suspend_reason`` is cleared the same way and for the same reason - a run that has
    since finished must not still claim to be waiting for quota.
    """
    existing = _existing_state(co.run_dir)
    co.run_dir.write_state(
        RunState(
            run_id=co.run_id,
            workflow=co.workflow,
            args=existing.args if existing is not None else _args_of(co),
            owner=existing.owner if existing is not None else by,
            status=status,
            cursor=cursor,
            cursor_payload_hash=cursor_payload_hash,
            suspend_reason=suspend_reason,
            policy_version=co.policy.version,
            tokens_by_row=dict(co.tokens_by_row),
        )
    )


def _existing_state(run_dir: RunDir) -> RunState | None:
    """Return the state file this run already has, or ``None`` before the first write."""
    if not run_dir.state_path.exists():
        return None
    return run_dir.read_state()


def _args_of(co: Coordinator) -> dict[str, Any]:
    """Render the coordinator's arguments as the JSON-ready mapping the state file holds."""
    args = co.args
    return args.model_dump(mode="json") if isinstance(args, BaseModel) else dict(args)
