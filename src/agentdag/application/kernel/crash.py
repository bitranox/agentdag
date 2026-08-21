"""The exit that writes nothing: noticing a run whose coordinator died, and saying so (design 3.4).

A coordinator has four exits and only three of them write. It returns and the run is
``done``; it raises ``Suspended`` and the run is ``suspended``; it raises anything else
and the run is ``failed``. The fourth is the PROCESS going away - ``SystemExit``, a
``SIGKILL``, the machine losing power - and there the run's own module writes nothing on
purpose, because that is what makes the next launch see a crash window rather than a tidy
failure (see :mod:`~agentdag.application.kernel.run`).

So ``state.json`` says ``running`` forever, and nothing tells the operator. This module is
the outside observer that closes that: a periodic pass, the same one that applies approve
deadlines, and for the same reason - a run nobody is watching is exactly the run that
needs an owner other than itself.

**How a crash is told from a run that is merely starting.** ``run start`` writes
``state=running`` BEFORE the background coordinator exists, let alone holds the lock, so
"state says running" alone would call every starting run a crash and mail the operator
each time. Two further facts are required, and together they are decisive:

* **The lock is free.** A live coordinator holds its run's lock for the whole launch, so
  taking it is proof no coordinator is running - and the lock port breaks a stale lock
  whose holder is gone, which is the case being detected. Failing to take it means
  somebody is alive, not that somebody died.
* **The journal is not empty.** The coordinator's first line is appended AFTER it takes
  the lock, so a non-empty journal proves a coordinator once held it. An empty one means
  no coordinator ever got that far: the run is starting, not dead.

The remaining window is a coordinator that dies between taking the lock and appending its
first line. It is not covered, deliberately: such a run did nothing at all, and widening
the rule to catch it would re-admit the false alarm on every ordinary start.

Recording ``crashed`` in the state file IS this module's dedup. A periodic pass sees every
run on every tick, and a crashed run keeps its state file indefinitely, so without the
write the operator would be told again every tick, forever. It also gives
:class:`~agentdag.domain.models.RunStatus`'s ``crashed`` its first producer - before this,
nothing in the system ever wrote that value.

Contents:
    * :class:`CrashOutcome` - what one pass over ONE run reports back.
    * :func:`record_crash` - record and announce a run whose coordinator died.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...domain.kernel_errors import LockHeld, RunRefused
from ...domain.models import RunStatus
from .notify import RunEvent, emit_best_effort
from .ports import stamp

if TYPE_CHECKING:
    from ...domain.models import LockHolder, RunState
    from .notify import Notifier
    from .ports import Clock, RunDir, RunLock

__all__ = ["CrashOutcome", "record_crash"]

_LOCK_HELD_REASON = "a coordinator holds the run's lock"
"""Why nothing was recorded when the lock could not be taken - which is the ordinary,
healthy answer for every live run, not a problem."""


@dataclass(frozen=True, slots=True)
class CrashOutcome:
    """What one crash check over one run reports back.

    Attributes:
        run_id: The run looked at.
        recorded: Whether this pass wrote ``crashed`` and emitted the event. ``False``
            for every healthy run, which is almost all of them.
        reason: Why nothing was recorded; ``""`` when something was.
    """

    run_id: str
    recorded: bool
    reason: str


def record_crash(
    run_dir: RunDir, *, lock: RunLock, holder: LockHolder, clock: Clock, notifier: Notifier
) -> CrashOutcome:
    """Record ``run_dir`` as ``crashed`` and tell the operator, if that is what it is.

    Writes at most one state file and emits at most one event; a run that is healthy,
    starting, live or already terminal is left exactly as it was. Safe to run over every
    run in a runs directory, on every tick.

    Args:
        run_dir: The run to look at; ``run_dir.root.name`` is its id.
        lock: The run's lock port - taking it is the liveness evidence, so it is held for
            the whole check and released before returning.
        holder: This process's identity, recorded as the lock's holder while held.
        clock: The one seam this reads wall-clock time through, for the event's stamp.
        notifier: Where the ``crashed`` event goes.

    Returns:
        What this pass did, or why it did nothing.
    """
    run_id = run_dir.root.name
    try:
        token = lock.acquire(run_dir.root, holder)
    except LockHeld:
        return CrashOutcome(run_id=run_id, recorded=False, reason=_LOCK_HELD_REASON)
    try:
        return _record_under_lock(run_dir, clock=clock, notifier=notifier)
    finally:
        lock.release(token)


def _record_under_lock(run_dir: RunDir, *, clock: Clock, notifier: Notifier) -> CrashOutcome:
    """Do the whole check with the lock held: read the state, judge it, write and announce."""
    run_id = run_dir.root.name
    unfinished = _unfinished_state(run_dir)
    if unfinished is None:
        return CrashOutcome(run_id=run_id, recorded=False, reason="not left running by a dead coordinator")
    if not _has_journalled(run_dir):
        return CrashOutcome(run_id=run_id, recorded=False, reason="starting: no coordinator has journalled yet")
    run_dir.write_state(unfinished.model_copy(update={"status": RunStatus.CRASHED}))
    emit_best_effort(
        notifier,
        RunEvent(run_id=run_id, workflow=unfinished.workflow, status=RunStatus.CRASHED, at=stamp(clock)),
    )
    return CrashOutcome(run_id=run_id, recorded=True, reason="")


def _unfinished_state(run_dir: RunDir) -> RunState | None:
    """Return the run's state when it still claims to be ``running``, else ``None``.

    An unreadable or missing state file is NOT reported as a crash: this pass runs over
    every directory under ``runs/``, so a half-written or foreign directory would
    otherwise be announced to the operator as a dead run.
    """
    try:
        state = run_dir.read_state()
    except (RunRefused, OSError, ValueError):
        return None
    return state if state.status is RunStatus.RUNNING else None


def _has_journalled(run_dir: RunDir) -> bool:
    """Return whether a coordinator ever appended to this run's journal.

    The evidence that separates a crashed run from a starting one - see the module
    docstring. Size rather than existence: the journal file can be created empty by an
    adapter that opens it before its first append, and an empty file is no evidence.
    """
    try:
        return run_dir.journal_path.stat().st_size > 0
    except OSError:
        return False
