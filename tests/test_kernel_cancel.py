"""Tests for whole-run cancel: the intent, the verified kill, the journal outcome (design 3.4, O25).

``request_cancel`` and ``resolve_cancel`` are exercised over the REAL run directory and
journal adapters (``FsRunDir``, ``JsonlJournal``, ``FileRunLock``) - only the
:class:`~agentdag.application.kernel.ports.Scope` a run is imagined to have started
under is a fake, because controlling exactly what a kill reports (never verified, or
verified) is the one seam this module's own RED/GREEN property (Step 1 of the task
brief) is about.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.cancel import (
    WHOLE_RUN_NODE_ID,
    CancelOutcome,
    request_cancel,
    resolve_cancel,
    scope_unit,
    sweep_stale_scope,
)
from agentdag.domain.journal import CancelLine, CancelRequestedLine
from agentdag.domain.kernel_errors import LockHeld, RunRefused
from agentdag.domain.models import RunState, RunStatus

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from agentdag.application.kernel.ports import LaunchResult, ScopeHandle


class FakeScope:
    """A bare :class:`~agentdag.application.kernel.ports.Scope` double: no real process, ever.

    ``kill_calls``/``is_alive_calls`` record every handle each method was asked about, so
    a test can prove :func:`~agentdag.application.kernel.cancel.resolve_cancel` reuses a
    JOURNALED answer rather than calling ``kill`` a second time.
    """

    def __init__(self, *, cross_process_capable: bool, alive: bool = True, kill_returns: bool = True) -> None:
        self.cross_process_capable = cross_process_capable
        self._alive = alive
        self._kill_returns = kill_returns
        self.is_alive_calls: list[ScopeHandle] = []
        self.kill_calls: list[ScopeHandle] = []

    def start(self, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> ScopeHandle:
        raise NotImplementedError("this fake is never asked to start a process")

    def confirm(self, handle: ScopeHandle, *, timeout_s: float) -> LaunchResult:
        raise NotImplementedError("this fake is never asked to confirm a launch")

    def is_alive(self, handle: ScopeHandle) -> bool:
        """Record the call and return the canned liveness."""
        self.is_alive_calls.append(handle)
        return self._alive

    def kill(self, handle: ScopeHandle) -> bool:
        """Record the call and return the canned kill result - NEVER trusted blindly by the caller."""
        self.kill_calls.append(handle)
        return self._kill_returns


def run_dir(tmp_path: Path, *, status: RunStatus = RunStatus.RUNNING) -> FsRunDir:
    """Lay out a fresh run directory whose ``state.json`` already carries ``status``."""
    rd = FsRunDir.create(tmp_path, "r1")
    rd.write_state(
        RunState(run_id="r1", workflow="graph-a", args={}, owner="tester", status=status, policy_version="sha256:p")
    )
    return rd


def journal_of(rd: FsRunDir) -> JsonlJournal:
    """Build the real journal adapter over ``rd``'s own paths."""
    return JsonlJournal(rd.journal_path, rd.audit_path)


def resolve(rd: FsRunDir, scope: FakeScope) -> CancelOutcome:
    """Call :func:`resolve_cancel` with the real lock/clock adapters, a fresh journal read."""
    return resolve_cancel(
        rd, journal_of(rd), scope=scope, lock=FileRunLock(), clock=UtcClock(), holder=current_holder()
    )


@pytest.mark.os_agnostic
def test_scope_unit_matches_the_launch_side_s_own_naming_rule() -> None:
    """No ``@`` (the template-instance separator systemd rejects) - a hyphen instead."""
    assert scope_unit("20260820T000000Z-abc123") == "agentdag-run-20260820T000000Z-abc123"
    assert "@" not in scope_unit("r1")


@pytest.mark.os_agnostic
def test_request_cancel_writes_the_intent_and_marks_the_run_cancelling(tmp_path: Path) -> None:
    rd = run_dir(tmp_path)

    outcome = request_cancel(rd, by="alice", token_id="tok-1")

    assert outcome.status is RunStatus.CANCELLING
    assert outcome.verified is False
    assert rd.read_state().status is RunStatus.CANCELLING
    assert (rd.root / "decisions" / "_run.cancel.json").is_file()
    intent_text = (rd.root / "decisions" / "_run.cancel.json").read_text(encoding="utf-8")
    assert '"by":"alice"' in intent_text.replace(" ", "") or '"by": "alice"' in intent_text


@pytest.mark.os_agnostic
def test_request_cancel_is_idempotent_once_already_cancelling(tmp_path: Path) -> None:
    """A second call on an already-cancelling run reports the SAME answer, writes nothing new."""
    rd = run_dir(tmp_path)
    request_cancel(rd, by="alice", token_id="tok-1")
    written_first = (rd.root / "decisions" / "_run.cancel.json").read_text(encoding="utf-8")

    again = request_cancel(rd, by="bob", token_id="tok-2")

    assert again.status is RunStatus.CANCELLING
    assert again.verified is False
    # Not overwritten by the second (different) caller - the intent already on disk stands.
    assert (rd.root / "decisions" / "_run.cancel.json").read_text(encoding="utf-8") == written_first


@pytest.mark.os_agnostic
@pytest.mark.parametrize("status", [RunStatus.DONE, RunStatus.FAILED])
def test_request_cancel_refuses_a_terminal_run(tmp_path: Path, status: RunStatus) -> None:
    rd = run_dir(tmp_path, status=status)

    with pytest.raises(RunRefused, match=status.value):
        request_cancel(rd, by="alice", token_id="tok-1")

    assert not (rd.root / "decisions" / "_run.cancel.json").is_file()


# ---------------------------------------------------------------------------------
# Step 1: the verified-cancel RED/GREEN property. A fake scope whose cgroup never
# empties must produce verified:false WITH a reason, never a bare true; the control is
# the SAME shape with a scope that does empty, which must produce verified:true.
# ---------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_resolve_cancel_never_reports_verified_true_when_the_scope_never_empties(tmp_path: Path) -> None:
    """RED: a scope whose ``kill`` reports failure must never be reported ``verified: true``."""
    rd = run_dir(tmp_path)
    request_cancel(rd, by="alice", token_id="tok-1")
    scope = FakeScope(cross_process_capable=True, alive=True, kill_returns=False)

    outcome = resolve(rd, scope)

    assert outcome.verified is False
    assert outcome.status is RunStatus.CANCELLING
    assert scope.kill_calls, "kill() must actually have been attempted"
    lines = journal_of(rd).lines()
    cancel_lines = [line for line in lines if isinstance(line, CancelLine)]
    assert len(cancel_lines) == 1
    assert cancel_lines[0].verified is False
    assert rd.read_state().status is RunStatus.CANCELLING


@pytest.mark.os_agnostic
def test_resolve_cancel_reports_verified_true_once_the_scope_confirms_empty(tmp_path: Path) -> None:
    """Control for the RED test above: the SAME shape, a scope that DOES empty."""
    rd = run_dir(tmp_path)
    request_cancel(rd, by="alice", token_id="tok-1")
    scope = FakeScope(cross_process_capable=True, alive=True, kill_returns=True)

    outcome = resolve(rd, scope)

    assert outcome.verified is True
    assert outcome.status is RunStatus.CANCELLED
    lines = journal_of(rd).lines()
    requested = [line for line in lines if isinstance(line, CancelRequestedLine)]
    cancelled = [line for line in lines if isinstance(line, CancelLine)]
    assert len(requested) == 1
    assert requested[0].run_id == "r1"
    assert requested[0].node_id is None
    assert requested[0].by == "alice"
    assert requested[0].token_id == "tok-1"
    assert len(cancelled) == 1
    assert cancelled[0].verified is True
    assert cancelled[0].node_id == WHOLE_RUN_NODE_ID
    assert rd.read_state().status is RunStatus.CANCELLED


@pytest.mark.os_agnostic
def test_resolve_cancel_skips_the_kill_entirely_when_the_scope_is_already_not_alive(tmp_path: Path) -> None:
    """Already gone (nothing to stop) is a genuinely verified success, not an unattempted kill."""
    rd = run_dir(tmp_path)
    request_cancel(rd, by="alice", token_id="tok-1")
    scope = FakeScope(cross_process_capable=True, alive=False)

    outcome = resolve(rd, scope)

    assert outcome.verified is True
    assert scope.is_alive_calls
    assert not scope.kill_calls


@pytest.mark.os_agnostic
def test_resolve_cancel_under_a_scope_that_cannot_verify_cross_process_reports_the_reason(tmp_path: Path) -> None:
    """The STOP condition: a scope that cannot confirm a cross-process kill at all never
    calls kill() (its return would be an untrustworthy default), and says why."""
    rd = run_dir(tmp_path)
    request_cancel(rd, by="alice", token_id="tok-1")
    scope = FakeScope(cross_process_capable=False)

    outcome = resolve(rd, scope)

    assert outcome.verified is False
    assert outcome.reason != ""
    assert "FakeScope" in outcome.reason
    assert not scope.is_alive_calls
    assert not scope.kill_calls
    cancel_lines = [line for line in journal_of(rd).lines() if isinstance(line, CancelLine)]
    assert cancel_lines[0].verified is False


@pytest.mark.os_agnostic
def test_resolve_cancel_leaves_the_run_cancelling_when_a_live_coordinator_holds_the_lock(tmp_path: Path) -> None:
    """A still-alive coordinator's lock is not an error: nothing is journaled, retry later."""
    rd = run_dir(tmp_path)
    request_cancel(rd, by="alice", token_id="tok-1")
    scope = FakeScope(cross_process_capable=True, alive=True, kill_returns=False)
    live_holder = current_holder()  # this test process itself: genuinely alive
    lock = FileRunLock()
    token = lock.acquire(rd.root, live_holder)

    try:
        with pytest.raises(LockHeld):
            lock.acquire(rd.root, current_holder())  # control: the lock really is held
        outcome = resolve_cancel(rd, journal_of(rd), scope=scope, lock=lock, clock=UtcClock(), holder=current_holder())
    finally:
        lock.release(token)

    assert outcome.verified is False
    assert outcome.status is RunStatus.CANCELLING
    assert "lock" in outcome.reason
    assert journal_of(rd).lines() == []  # nothing journaled while the lock was unavailable


@pytest.mark.os_agnostic
def test_resolve_cancel_called_again_after_success_reuses_the_journaled_answer_not_a_fresh_kill(tmp_path: Path) -> None:
    rd = run_dir(tmp_path)
    request_cancel(rd, by="alice", token_id="tok-1")
    scope = FakeScope(cross_process_capable=True, alive=True, kill_returns=True)
    first = resolve(rd, scope)
    assert first.verified is True
    assert len(scope.kill_calls) == 1

    second = resolve(rd, scope)

    assert second.verified is True
    assert second.status is RunStatus.CANCELLED
    assert len(scope.kill_calls) == 1  # not called again
    assert len([line for line in journal_of(rd).lines() if isinstance(line, CancelLine)]) == 1  # not duplicated


# ---------------------------------------------------------------------------------
# The startup sweep (Step 4): stop a scope left behind by a dead coordinator before a
# new one starts. Writes no journal line of its own - unlike resolve_cancel, this is
# unconditional housekeeping, not a record of a cancel someone asked for.
# ---------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_sweep_stale_scope_kills_a_scope_still_alive(tmp_path: Path) -> None:
    rd = run_dir(tmp_path)
    scope = FakeScope(cross_process_capable=True, alive=True, kill_returns=True)

    sweep_stale_scope(rd, scope=scope)

    assert scope.kill_calls
    assert journal_of(rd).lines() == []  # housekeeping only, never journaled


@pytest.mark.os_agnostic
def test_sweep_stale_scope_is_a_no_op_when_the_scope_is_already_gone(tmp_path: Path) -> None:
    rd = run_dir(tmp_path)
    scope = FakeScope(cross_process_capable=True, alive=False)

    sweep_stale_scope(rd, scope=scope)

    assert scope.is_alive_calls
    assert not scope.kill_calls


@pytest.mark.os_agnostic
def test_sweep_stale_scope_never_calls_a_scope_that_cannot_verify_cross_process(tmp_path: Path) -> None:
    """A fresh ``run start``'s own brand-new run_id is always this shape, safely a no-op too."""
    rd = run_dir(tmp_path)
    scope = FakeScope(cross_process_capable=False)

    sweep_stale_scope(rd, scope=scope)

    assert not scope.is_alive_calls
    assert not scope.kill_calls
