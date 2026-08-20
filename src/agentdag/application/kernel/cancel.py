"""Cancel a whole run: the intent, its kill, and the verified journal outcome (design 3.4, O25).

``run.cancel`` is one of the three verbs the mcp-surface finding O25 exists for
("run.cancel blocks for the node's whole deadline"): a synchronous call held open for
up to ``run_limits.deadline_ceiling_s`` is the one verb an operator reaches for and the
least likely to survive a proxy or a client timeout. So the shape here is the same two
steps ``mcp-surface.md`` section 3.8 and ``2026-08-17-agentdag-design.md`` section 6
("cancel") describe:

1. :func:`request_cancel` WRITES the cancel intent as a file under ``decisions/``
   (``decisions/_run.cancel.json``, the same write discipline
   :meth:`~agentdag.application.kernel.ports.RunDir.write_decision` uses for an approve
   decision - never a journal append, since the journal keeps its one writer) and
   returns AT ONCE with the run marked ``cancelling``.
2. :func:`resolve_cancel` does the actual work, on whichever process gets to run it:
   kill the run's scope (verified, never the stop verb's own return value taken on
   trust - see :attr:`~agentdag.application.kernel.ports.Scope.cross_process_capable`),
   then - ONLY once the run's lock can be taken, meaning no OTHER coordinator is still
   alive to be racing this journal write - fold the intent into the journal as
   :class:`~agentdag.domain.journal.CancelRequestedLine` and record the verified outcome
   as :class:`~agentdag.domain.journal.CancelLine`. A live coordinator still holding the
   lock is not an error here: this simply reports the run is still ``cancelling`` and
   leaves the retry to a LATER call (another ``run cancel``, or
   :func:`sweep_stale_scope` on the run's next relaunch attempt).

:func:`sweep_stale_scope` is the narrower, unconditional housekeeping half (design 3.1's
"a startup sweep stops scopes whose coordinator is gone"): called before ANY relaunch,
CRASHED or CANCELLING alike, it stops a scope still draining from a dead coordinator so a
NEW coordinator never starts dispatching into a worktree, gate lock or credential store a
zombie sibling is still touching - the M2 crash probe measured this window as roughly 40
seconds of executor children outliving their killed coordinator. It writes no journal line
of its own (that is :func:`resolve_cancel`'s job, for a run that was actually asked to
cancel); a fresh run's own unit was never started under this ``run_id`` at all, so
sweeping it is always a safe no-op.

Contents:
    * :func:`scope_unit` - the deterministic scope unit name a launch and a later kill
      both compute from a bare ``run_id`` alone.
    * :class:`CancelOutcome` - what one call into this module reports back.
    * :func:`request_cancel` - write the intent, mark the run ``cancelling``.
    * :func:`resolve_cancel` - kill the scope and journal the verified outcome.
    * :func:`sweep_stale_scope` - stop a scope left behind by a dead coordinator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...domain.journal import CancelLine, CancelRequestedLine
from ...domain.kernel_errors import LockHeld, RunRefused
from ...domain.models import CancelIntent, RunStatus
from .ports import ScopeHandle, stamp

if TYPE_CHECKING:
    from ...domain.models import LockHolder
    from .ports import Clock, Journal, RunDir, RunLock, Scope

__all__ = ["WHOLE_RUN_NODE_ID", "CancelOutcome", "request_cancel", "resolve_cancel", "scope_unit", "sweep_stale_scope"]

_INTENT_REL_PATH = "decisions/_run.cancel.json"
"""Where a whole-run cancel intent lives, matching ``run_store_fs.FsRunDir.decision_files``'s
own reserved-shape doc (``_run.cancel.json``) and its exclusion from ``decisions/``'s
normal approve-decision listing."""

WHOLE_RUN_NODE_ID = "_run"
"""The sentinel :class:`~agentdag.domain.journal.CancelLine`'s required, non-empty
``node_id`` uses for a whole-run cancel - ``journal-line.schema.json``'s ``cancel_line``
carries no nullable-node_id affordance the way ``cancel_requested_line`` does, so this
mirrors the SAME string the reserved intent filename already uses for "the whole run"."""

_TERMINAL_STATUSES = (RunStatus.DONE, RunStatus.FAILED)
_ALREADY_REQUESTED_STATUSES = (RunStatus.CANCELLING, RunStatus.CANCELLED)

_UNKNOWN = "unknown"
"""What :func:`resolve_cancel` folds a cancel_requested line's ``by``/``token_id`` as if
the intent file is unexpectedly missing by the time it runs - defensive only: every
caller writes the intent (:func:`request_cancel`) strictly before this can run."""


def scope_unit(run_id: str) -> str:
    """Return the scope unit name a coordinator launch and a later kill both use for ``run_id``.

    A HYPHEN, never ``@``: measured live on ``lxc-pydev`` while building Task 17
    (``adapters.kernel.scope_systemd``'s own module docstring),
    ``systemd-run --user --scope --unit=agentdag-run@<run_id>`` fails outright ("Invalid
    argument") because systemd reads ``@`` in a unit name as the template-instance
    separator. The SAME function two different call sites need to agree on: the CLI's own
    ``run start``/``_relaunch`` (which STARTS the unit this names) and this module's
    :func:`resolve_cancel`/:func:`sweep_stale_scope` (which KILL it, in a later, separate
    process that has only the bare ``run_id`` to reconstruct a
    :class:`~agentdag.application.kernel.ports.ScopeHandle` from) - a naming drift between
    the two would make a kill silently target a unit that was never started.

    Args:
        run_id: The run's id.

    Returns:
        The unit's BASE name; :class:`~agentdag.adapters.kernel.scope_systemd.SystemdScope`
        appends ``.scope`` itself.

    Example:
        >>> scope_unit("20260820T000000Z-abc123")
        'agentdag-run-20260820T000000Z-abc123'
    """
    return f"agentdag-run-{run_id}"


def _handle_for(run_dir: RunDir, run_id: str) -> ScopeHandle:
    """Reconstruct the :class:`~agentdag.application.kernel.ports.ScopeHandle` a kill needs.

    ``pid`` and ``log_path`` are never read by :meth:`Scope.is_alive`/:meth:`Scope.kill`
    (both key off ``handle.unit`` alone - verified directly against both adapters) so
    they carry harmless placeholders here rather than the real values a fresh process,
    which never itself launched this unit, has no way to know.
    """
    return ScopeHandle(unit=scope_unit(run_id), pid=0, log_path=run_dir.root / "launch.log")


@dataclass(frozen=True, slots=True)
class CancelOutcome:
    """What one call into this module reports back about a whole-run cancel.

    Attributes:
        status: The run's status as this call left it - ``cancelling`` until
            :attr:`verified` is ``True``, then ``cancelled``.
        verified: Whether the run's scope is CONFIRMED empty - never the stop verb's own
            return value taken on trust for a :class:`~agentdag.application.kernel.ports.Scope`
            that cannot verify a cross-process kill at all (see
            :attr:`~agentdag.application.kernel.ports.Scope.cross_process_capable`).
        reason: Why :attr:`verified` is ``False`` when it is meaningfully unconfirmable
            (the scope kind cannot verify a cross-process kill, or another coordinator
            still holds the run's lock) - empty when :attr:`verified` is ``True``, or
            when this is simply the FIRST report right after :func:`request_cancel`
            (nothing has been attempted to verify yet).
    """

    status: RunStatus
    verified: bool
    reason: str


def request_cancel(run_dir: RunDir, *, by: str, token_id: str) -> CancelOutcome:
    """Write the whole-run cancel intent (idempotent) and mark the run ``cancelling``.

    Returns AT ONCE (mcp-surface O25): does not touch the run's scope at all, so it never
    blocks on the kill's own poll budget. Idempotent per ``run.cancel``'s own contract
    (mcp-surface.md 3.8): a second call on a run already ``cancelling``/``cancelled``
    writes nothing new and reports the CURRENT status back unchanged, rather than
    re-writing an intent that already exists.

    Args:
        run_dir: The run to cancel.
        by: The verified identity requesting the cancel.
        token_id: The credential it was authorised with.

    Returns:
        ``CancelOutcome(status=CANCELLING, verified=False, reason="")`` for a fresh
        request; the run's UNCHANGED current status for one already cancelling or
        cancelled (``verified`` reflects whether it is fully ``cancelled`` yet).

    Raises:
        RunRefused: the run is already ``done`` or ``failed`` - there is nothing left to
            cancel, and resuming it into a fresh cancel intent would contradict a
            terminal state nothing can undo.
    """
    state = run_dir.read_state()
    if state.status in _TERMINAL_STATUSES:
        raise RunRefused(f"run {run_dir.root.name} is {state.status.value}; nothing to cancel")
    if state.status in _ALREADY_REQUESTED_STATUSES:
        return CancelOutcome(status=state.status, verified=state.status is RunStatus.CANCELLED, reason="")
    intent = CancelIntent(run_id=run_dir.root.name, by=by, token_id=token_id)
    run_dir.write_atomic(_INTENT_REL_PATH, intent.model_dump_json(indent=1))
    run_dir.write_state(state.model_copy(update={"status": RunStatus.CANCELLING}))
    return CancelOutcome(status=RunStatus.CANCELLING, verified=False, reason="")


def resolve_cancel(
    run_dir: RunDir, journal: Journal, *, scope: Scope, lock: RunLock, clock: Clock, holder: LockHolder
) -> CancelOutcome:
    """Kill the run's scope (best-effort, verified) and journal the outcome, if the lock is free.

    Two independent things can each fail without the other, and this reports both
    honestly rather than collapsing them into one boolean:

    * the KILL may not be verifiable at all (:attr:`~agentdag.application.kernel.ports.Scope.
      cross_process_capable` is ``False``) or may simply not have finished emptying the
      cgroup within its own poll budget - either way ``verified`` comes back ``False``
      with :attr:`CancelOutcome.reason` naming which;
    * the JOURNAL WRITE needs this run's lock, which a still-alive coordinator may hold
      (only possible when the kill itself could not verify success, since a VERIFIED
      kill means the whole cgroup - coordinator included - is confirmed gone). When the
      lock cannot be taken, NOTHING is journaled here: the run stays ``cancelling``, and
      a LATER call (another ``run cancel``, or :func:`sweep_stale_scope` on the next
      relaunch attempt reclaiming a since-stale lock) tries again.

    Never re-journals a cancel already recorded (checked against the journal itself, not
    ``state.json``) - a caller may retry this after a failed lock acquisition without
    risking a duplicate pair of lines once a concurrent caller already succeeded.

    Args:
        run_dir: The run being cancelled; ``run_dir.root.name`` is its id.
        journal: This run's journal - read once to check for an already-recorded cancel,
            appended to at most once (a ``cancel_requested`` line, then a ``cancel`` line)
            when the lock is actually taken.
        scope: The scope kind this run started under (re-derived by the caller the same
            way ``run start`` originally chose it - nothing persists which kind a run
            actually used).
        lock: The run's lock port.
        clock: What :func:`~agentdag.application.kernel.ports.stamp` reads for both
            appended lines' ``at``.
        holder: This process's own identity, recorded as the lock's holder for as long as
            this call holds it - released before returning either way.

    Returns:
        The run's status and verification state after this attempt.
    """
    already = _already_resolved_verified(journal)
    if already is not None:
        return CancelOutcome(status=_status_for(verified=already), verified=already, reason="")
    verified, reason = _kill(scope, run_dir)
    try:
        token = lock.acquire(run_dir.root, holder)
    except LockHeld:
        return CancelOutcome(
            status=RunStatus.CANCELLING, verified=False, reason="a coordinator still holds the run lock"
        )
    try:
        still_unresolved = _already_resolved_verified(journal) is None
        if still_unresolved:
            _journal_cancel(run_dir, journal, clock=clock, verified=verified)
    finally:
        lock.release(token)
    return CancelOutcome(status=_status_for(verified=verified), verified=verified, reason=reason)


def _status_for(*, verified: bool) -> RunStatus:
    """Render a verification result as the run's status: ``cancelled`` only once verified."""
    return RunStatus.CANCELLED if verified else RunStatus.CANCELLING


def sweep_stale_scope(run_dir: RunDir, *, scope: Scope) -> None:
    """Stop a scope left behind by a dead coordinator, verified, before a new one may start.

    Called before every relaunch (``run start``'s own fresh run_id included, for which
    this is always a safe no-op - that unit was never started under any scope kind), so a
    NEW coordinator never begins dispatching while a zombie sibling's grandchildren (the
    M2 crash probe measured roughly 40 seconds of these outliving a killed coordinator)
    are still touching the same worktrees, gate lock or credential store.

    Writes no journal line: unlike :func:`resolve_cancel`, this is unconditional
    housekeeping for ANY dead coordinator (a crash, an interrupted cancel, ordinary
    process death), not a record of a cancel someone asked for.

    Args:
        run_dir: The run about to be started or resumed.
        scope: The scope kind this launch will use.
    """
    if not scope.cross_process_capable:
        return  # nothing this scope kind can verify about a PRIOR invocation's own unit
    handle = _handle_for(run_dir, run_dir.root.name)
    if scope.is_alive(handle):
        scope.kill(handle)  # best-effort: blocks until verified empty, or its own poll budget elapses


def _kill(scope: Scope, run_dir: RunDir) -> tuple[bool, str]:
    """Kill ``run_dir``'s scope if this scope kind can verify a cross-process kill at all.

    Returns:
        ``(True, "")`` once confirmed empty (already gone, or killed and polled empty);
        ``(False, reason)`` when the scope kind cannot verify a cross-process kill at
        all, or when it tried and the cgroup was not confirmed empty within its own poll
        budget.
    """
    if not scope.cross_process_capable:
        return False, f"{type(scope).__name__} cannot verify a kill across two separate process invocations"
    handle = _handle_for(run_dir, run_dir.root.name)
    if not scope.is_alive(handle):
        return True, ""
    return scope.kill(handle), ""


def _already_resolved_verified(journal: Journal) -> bool | None:
    """Return the ``verified`` value of this run's journaled :class:`CancelLine`, or ``None``.

    ``None`` means no such line exists yet - a caller must still attempt the kill and the
    journal write. When one DOES exist, its own recorded ``verified`` is returned rather
    than re-derived from a fresh kill attempt: a repeat call (a race between two ``run
    cancel`` invocations, or a retry after this run's lock became free) reports the SAME
    answer the first successful resolution already recorded, instead of quietly re-running
    ``scope.kill`` against a unit that may already be long gone.
    """
    for line in journal.lines():
        if isinstance(line, CancelLine):
            return line.verified
    return None


def _journal_cancel(run_dir: RunDir, journal: Journal, *, clock: Clock, verified: bool) -> None:
    """Fold the intent, then the verified outcome, into the journal; update ``state.json``.

    Called only once the run's lock is held (this process is, for this moment, "the
    coordinator" in the same sense any relaunch reclaiming a stale lock already is - the
    journal's single-writer discipline is about the LOCK, not one eternal process).
    """
    intent = _read_intent(run_dir)
    by = intent.by if intent is not None else _UNKNOWN
    token_id = intent.token_id if intent is not None else _UNKNOWN
    journal.append(
        CancelRequestedLine(run_id=run_dir.root.name, node_id=None, by=by, token_id=token_id, at=stamp(clock))
    )
    journal.append(CancelLine(node_id=WHOLE_RUN_NODE_ID, verified=verified, at=stamp(clock)))
    state = run_dir.read_state()
    run_dir.write_state(state.model_copy(update={"status": _status_for(verified=verified)}))


def _read_intent(run_dir: RunDir) -> CancelIntent | None:
    """Read back the whole-run cancel intent :func:`request_cancel` wrote, or ``None``."""
    try:
        text = run_dir.read_text(_INTENT_REL_PATH)
    except FileNotFoundError:
        return None
    return CancelIntent.model_validate_json(text)
