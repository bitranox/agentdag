"""Start or resume one run: the lock, the journal's bookend lines, the state file (design 3.4).

One launch of a coordinator, whether it is a run's first or its fifth. Everything that
distinguishes the two is read from the journal, never passed in: an empty journal makes
this a start (``run_started``), a non-empty one makes it a resume (``resume``), and the
replay index folded from it decides which nodes the program's re-execution actually
dispatches.

Three exits, and the fourth that is not an exit at all:

* the program returns   -> a ``run_summary`` line, state ``done``, cursor cleared;
* it raises ``Suspended`` -> state ``suspended``, cursor at the approve node, no summary;
* it raises anything else -> state ``failed``, and the exception propagates;
* the PROCESS dies (``SystemExit``, ``KeyboardInterrupt``) -> nothing is written, so the
  state on disk stays ``running`` with a ``started`` line that has no ``result``. That IS
  the crash window, and it is what the next launch re-dispatches. The lock is still
  released, because releasing it is the only thing that has to happen either way.

Contents:
    * :class:`RunOutcome` - what one launch reports back to its caller.
    * :func:`run_coordinator` - run one launch to one of the three exits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from ...domain.errors import RunRefused, Suspended
from ...domain.journal import ResumeLine, RunStartedLine
from ...domain.models import RunState, RunStatus
from .context import Coordinator
from .dispatch import Dispatcher
from .ports import stamp
from .summary import run_summary_line
from .workflow_check import assert_deterministic

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ...domain.models import LockHolder
    from ..graph_a_ports import GatePort, GitPort
    from ..workflows import WorkflowDef
    from .ports import Clock, Executor, IsolationScanner, Journal, Policy, RunDir, RunLock

__all__ = ["RunOutcome", "run_coordinator"]

_ResumeReason = Literal["decision", "crash", "restart", "manual"]
_RESUME_REASONS: tuple[_ResumeReason, ...] = ("decision", "crash", "restart", "manual")


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What one launch of a coordinator reports back.

    Attributes:
        status: The run's status as this launch left it on disk.
        suspended_node: The approve node the run is waiting on, or ``None``.
        dispatched_keys: Every key this launch put through the dispatcher, served keys
            included and in order - the replay-purity oracle: a launch that serves
            everything must produce exactly the journal's own ``started`` keys.
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
    parallel: int,
    by: str,
    token_id: str,
    resume_reason: str | None,
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
        parallel: How many map branches may run at once.
        by: Who launched this; recorded on the opening journal line and, on a first
            start only, as the run's owner.
        token_id: The credential this launch authenticated with, recorded likewise.
        resume_reason: Why this launch is a resume (``decision``, ``crash``,
            ``restart`` or ``manual``); ignored on a run's first start.

    Returns:
        The launch's outcome.

    Raises:
        LockHeld: another live coordinator holds ``run_dir``.
        NondeterministicCallError: ``workflow``'s module reaches for the clock or randomness.
        RunRefused: ``resume_reason`` is not one of the four known reasons.
        Exception: whatever the program raised, after the run is marked ``failed``.
    """
    token = lock.acquire(run_dir.root, holder)
    try:
        assert_deterministic(workflow.module)
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
            parallel=parallel,
        )
        co.fold_decisions()
        _write_state(co, status=RunStatus.RUNNING, cursor=None, by=by)
        return await _drive(co, workflow=workflow, args=args, by=by, replay_seconds=replay_seconds)
    finally:
        lock.release(token)


async def _drive(
    co: Coordinator, *, workflow: WorkflowDef, args: BaseModel, by: str, replay_seconds: float | None
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
        _write_state(co, status=RunStatus.SUSPENDED, cursor=suspended.node_id, by=by)
        return RunOutcome(RunStatus.SUSPENDED, suspended.node_id, list(co.dispatcher.dispatched_keys))
    except Exception:
        _write_state(co, status=RunStatus.FAILED, cursor=None, by=by)
        raise
    _summarise(co, replay_seconds=replay_seconds)
    _write_state(co, status=RunStatus.DONE, cursor=None, by=by)
    return RunOutcome(RunStatus.DONE, None, list(co.dispatcher.dispatched_keys))


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
    """Narrow a caller's resume reason to the four the journal line accepts.

    Args:
        resume_reason: The reason given, or ``None`` for the unremarkable case.

    Returns:
        The reason, defaulting to ``"manual"``.

    Raises:
        RunRefused: the reason is not one of the four.

    Example:
        >>> _reason(None), _reason("crash")
        ('manual', 'crash')
    """
    if resume_reason is None:
        return "manual"
    if resume_reason not in _RESUME_REASONS:
        raise RunRefused(f"resume reason {resume_reason!r} is not one of {list(_RESUME_REASONS)}")
    return resume_reason


def _write_state(co: Coordinator, *, status: RunStatus, cursor: str | None, by: str) -> None:
    """Write ``state.json`` for this launch, keeping what only the first start decides.

    ``args`` and ``owner`` are read back from an existing state file rather than
    rewritten: they are the run's identity, and a relaunch by somebody else with a
    re-parsed argument set must not silently redefine what the run IS.
    ``tokens_by_row`` is the opposite - it is OVERWRITTEN with the coordinator's own
    totals, which are rebuilt from zero on every launch by charging every record the
    launch touched, served ones included. Adding them to what the file already held
    would count the same records once per launch.
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


def _summarise(co: Coordinator, *, replay_seconds: float | None) -> None:
    """Append the run's summary line, measured over the journal as it stands BEFORE it.

    The line's own size cannot be counted in the figures it reports, so
    ``journal_bytes`` and ``journal_lines`` describe the journal this summary closes,
    not the file that ends up on disk.
    """
    journal = co.dispatcher.journal
    lines = journal.lines()
    journal.append(
        run_summary_line(
            run_id=co.run_id,
            policy_version=co.policy.version,
            records=[line.record for line in lines if line.event == "result"],
            journal_bytes=co.run_dir.journal_path.stat().st_size,
            journal_lines=len(lines),
            replay_seconds=replay_seconds,
            human_interactions=co.interactions,
            tokens_by_row=co.tokens_by_row,
            at=stamp(co.clock),
            brief_lengths=_brief_lengths(co.run_dir.root),
        )
    )


def _brief_lengths(root: Path) -> dict[str, int]:
    """Map node id -> the length in characters of the LONGEST brief written for it.

    A node dispatched more than once has one ``brief.md`` per key. The longest is taken
    because the length is only ever subtracted from a measured first turn to estimate
    dispatch overhead, so the largest brief is the reading that never OVERSTATES the
    overhead this signal exists to watch.
    """
    lengths: dict[str, int] = {}
    for brief in (root / "nodes").glob("*/*/brief.md"):
        node_id = brief.parent.parent.name
        lengths[node_id] = max(lengths.get(node_id, 0), len(brief.read_text(encoding="utf-8")))
    return lengths
