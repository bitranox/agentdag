"""The subtree stop scope: who is being asked to stop, and who has already left.

Task 34 step 1. The scope is read from another task's coroutine on every matched tool
use, so what these arms pin is membership over time rather than a snapshot: a node that
has already gone terminal must not be notified, and a node that starts AFTER the stop was
requested must be.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agentdag.application.kernel.subtree import BARRIER_SLACK_S, StopScope, barrier, deadline_bound
from agentdag.domain.models import NodeStatus


def test_request_stop_covers_every_in_flight_node_and_nothing_else() -> None:
    """Only nodes still in flight are notified; one that already landed is not."""
    scope = StopScope()
    scope.enter("a", _T0)
    scope.enter("b", _T0)
    scope.leave("b", NodeStatus.DONE)

    assert scope.request_stop() == frozenset({"a"})
    assert scope.is_stopping("a")
    assert not scope.is_stopping("b")


def test_a_node_entering_after_the_stop_is_already_stopping() -> None:
    """A late entrant must not slip past the notice.

    The subtree is STOPPING; the membership list is not frozen at the moment of the call.
    A node launched between ``request_stop`` and the barrier would otherwise run
    unnotified and the barrier would then wait out its full deadline.
    """
    scope = StopScope()
    scope.request_stop()
    scope.enter("late", _T0)

    assert scope.is_stopping("late")


def test_a_node_that_never_entered_is_not_stopping() -> None:
    """The predicate answers for THIS subtree only.

    Without this, an executor sharing one scope across sibling subtrees would notify a
    node that belongs to a different parent. Absence is a real answer, not a default.
    """
    scope = StopScope()
    scope.enter("mine", _T0)
    scope.request_stop()

    assert not scope.is_stopping("someone-elses")


def test_in_flight_tracks_entry_and_exit() -> None:
    """``in_flight`` is what the barrier waits on, so it must shrink as nodes land."""
    scope = StopScope()
    scope.enter("a", _T0)
    scope.enter("b", _T0)
    assert scope.in_flight() == frozenset({"a", "b"})

    scope.leave("a", NodeStatus.FAILED)
    assert scope.in_flight() == frozenset({"b"})


def test_leaving_after_a_stop_clears_the_node_from_in_flight() -> None:
    """A notified node that terminates leaves the barrier's wait set.

    This is the arm that lets the barrier return empty rather than timing out: stopping
    is a property of the SUBTREE, but ``in_flight`` is a property of the NODE, and a
    stopped node still has to be able to finish.
    """
    scope = StopScope()
    scope.enter("a", _T0)
    scope.request_stop()
    assert scope.in_flight() == frozenset({"a"})

    scope.leave("a", NodeStatus.DONE)
    assert scope.in_flight() == frozenset()


class _Spec:
    """The one field `deadline_bound` reads. A real NodeSpec carries far more, and
    building one here would couple this arm to fields it does not exercise."""

    def __init__(self, deadline_s: float | None) -> None:
        self.deadline_s = deadline_s


def test_the_barrier_returns_empty_once_every_node_is_terminal() -> None:
    """Empty is the success case: every node went terminal inside the bound."""
    scope = StopScope()
    scope.enter("a", _T0)
    scope.request_stop()

    async def drive() -> frozenset[str]:
        async def land() -> None:
            await asyncio.sleep(0)
            scope.leave("a", NodeStatus.DONE)

        task = asyncio.ensure_future(land())
        out = await barrier(scope, deadline_bound_s=5.0)
        await task
        return out

    assert asyncio.run(drive()) == frozenset()


def test_the_barrier_reports_who_was_still_running_on_timeout() -> None:
    """The important arm. A barrier that returned success on a timeout would let a
    re-plan run against a worktree a node is still writing to, which is the exact race
    the barrier exists to prevent. Report the timeout; never treat it as done."""
    scope = StopScope()
    scope.enter("stuck", _T0)
    scope.request_stop()

    assert asyncio.run(barrier(scope, deadline_bound_s=0.05)) == frozenset({"stuck"})


def test_the_barrier_does_not_cancel_the_node_it_gave_up_on() -> None:
    """Design constraint 2: a stopped node is not killed. After a timeout the node must
    still be running - if the barrier cancelled it, its handover would be lost, which is
    the whole reason the notice precedes the drain."""
    scope = StopScope()
    scope.enter("stuck", _T0)
    scope.request_stop()

    async def drive() -> tuple[frozenset[str], bool, bool]:
        still_running = asyncio.Event()

        async def never_lands() -> None:
            await asyncio.sleep(0.2)
            still_running.set()

        task = asyncio.ensure_future(never_lands())
        out = await barrier(scope, deadline_bound_s=0.01)
        cancelled = task.cancelled()
        await task
        return out, cancelled, still_running.is_set()

    stuck, cancelled, ran_on = asyncio.run(drive())
    assert stuck == frozenset({"stuck"})
    assert not cancelled
    assert ran_on


def test_the_bound_is_derived_from_the_in_flight_deadlines_not_a_constant() -> None:
    """Checkpoint B: the bound must MOVE with the nodes. A subtree of 60s nodes and one
    of 3600s nodes must not get the same bound, or it is a constant wearing a
    derivation's name. Asserted on the DIFFERENCE, so the slack cancels and the arm
    cannot pass by both values happening to be the slack."""
    short = deadline_bound(_scope_of("s"), {"s": _Spec(60.0)}, now=_T0)
    long_ = deadline_bound(_scope_of("l"), {"l": _Spec(3600.0)}, now=_T0)

    assert long_ - short == pytest.approx(3540.0, abs=1.0)


def test_the_bound_takes_the_remaining_deadline_not_the_whole_one() -> None:
    """The decision this module was reworked for (user, 2026-08-30).

    A node 3599s into a 3600s deadline has ONE second left. Bounding the wait at its whole
    deadline is only free while the wait ends early; in the one case the barrier exists for
    - deadline enforcement itself having failed - the wait runs to the full bound, so the
    stuck node is reported ~3630s later instead of ~31s.
    """
    scope = _scope_of("nearly_done")
    bound = deadline_bound(scope, {"nearly_done": _Spec(3600.0)}, now=_at(3599.0))

    assert bound == pytest.approx(1.0 + BARRIER_SLACK_S)


def test_the_largest_remaining_governs_not_the_largest_declared() -> None:
    """The arm that separates the two rules rather than merely exercising one.

    ``nearly_done`` declares the LONGER deadline and ``just_started`` the shorter one, so a
    bound over declared deadlines picks 900s while a bound over remaining time picks 100s.
    Both nodes are in flight, so a rule that read either one alone cannot pass this.
    """
    scope = StopScope()
    scope.enter("nearly_done", _T0)
    scope.enter("just_started", _at(890.0))
    graph = {"nearly_done": _Spec(900.0), "just_started": _Spec(100.0)}

    bound = deadline_bound(scope, graph, now=_at(890.0))

    assert bound == pytest.approx(100.0 + BARRIER_SLACK_S)


def test_a_node_already_past_its_deadline_contributes_only_the_slack() -> None:
    """A node still in flight past its own deadline contributes no time at all.

    That node is the deadline-enforcement failure the returned set exists to report, and
    what is left for it is the slack alone - the room for the interrupt to land and the
    record to be written. This arm does NOT pin the floor inside ``_remaining_s``: the
    bound's accumulator starts at 0.0 and absorbs a negative remainder either way, so the
    floor is pinned by that helper's own doctest instead. Verified by mutation.
    """
    scope = _scope_of("overdue")
    bound = deadline_bound(scope, {"overdue": _Spec(60.0)}, now=_at(600.0))

    assert bound == pytest.approx(BARRIER_SLACK_S)


def test_a_clock_that_stepped_back_never_inflates_the_bound() -> None:
    """Elapsed time floors at zero too, so remaining never exceeds the whole deadline.

    The start times come from the wall-clock ``Clock`` port, matching what the executor's
    own deadline check measures against, and a wall clock can step backwards under NTP.
    Without this an apparently-negative elapsed would hand back MORE than the node was ever
    given, which is the over-estimate direction but unbounded.
    """
    scope = _scope_of("stepped", at=_at(120.0))
    bound = deadline_bound(scope, {"stepped": _Spec(60.0)}, now=_T0)

    assert bound == pytest.approx(60.0 + BARRIER_SLACK_S)


def test_the_scope_carries_each_node_s_start_and_drops_it_on_leave() -> None:
    """The start cannot be forgotten because entering IS stamping.

    ``deadline_bound`` reads its start times from here rather than from a mapping the
    caller assembles alongside, so there is no in-flight node without a start to decide an
    absent case for. The stamp must also leave with the node, or a landed node would keep
    bounding a wait it is no longer part of.
    """
    scope = StopScope()
    scope.enter("a", _T0)
    scope.enter("b", _at(30.0))
    assert scope.in_flight_since() == {"a": _T0, "b": _at(30.0)}

    scope.leave("a", NodeStatus.DONE)
    assert scope.in_flight_since() == {"b": _at(30.0)}


def test_the_snapshot_of_starts_does_not_change_under_the_caller() -> None:
    """`in_flight_since` hands back a snapshot, not the live mapping.

    A caller iterating it while a node lands would otherwise see the mapping mutate
    mid-iteration - the same reason the execute loop snapshots the graph before dispatch.
    """
    scope = StopScope()
    scope.enter("a", _T0)
    snapshot = scope.in_flight_since()

    scope.leave("a", NodeStatus.DONE)

    assert snapshot == {"a": _T0}


def test_a_node_with_no_deadline_falls_back_to_the_policy_ceiling_and_counts_down() -> None:
    """`deadline_s` is None for a node that declares none. Treating that as 0.0 would make
    the barrier give up instantly and report it stuck, which reads as a deadline-enforcement
    failure when it is just an undeclared deadline.

    The fallback is the EXISTING `Policy.deadline_ceiling_s`, not a new knob: Checkpoint B
    required the bound be derived, and a node with no deadline of its own is bounded by the
    ceiling that clamps every node anyway. The ceiling STANDS IN for the node's deadline, so
    it counts down from the node's start like a declared one - asserted with elapsed time on
    the clock so a fallback that skipped the subtraction could not pass."""
    scope = _scope_of("undeclared")

    bound = deadline_bound(scope, {"undeclared": _Spec(None)}, now=_at(200.0), ceiling_s=1200.0)

    assert bound == pytest.approx(1000.0 + BARRIER_SLACK_S)


def test_an_undeclared_deadline_with_no_ceiling_is_reported_not_invented() -> None:
    """With neither a node deadline nor a ceiling there is nothing to derive from. The
    barrier must not invent a number: it says so, so the caller cannot mistake a guess for
    a bound."""
    scope = _scope_of("undeclared")

    with pytest.raises(ValueError, match="no deadline"):
        deadline_bound(scope, {"undeclared": _Spec(None)}, now=_T0)


def test_the_bound_of_an_empty_scope_is_the_slack_alone() -> None:
    """Nothing in flight means nothing to wait for. Decided deliberately rather than
    inherited: the empty case is not vacuously the same as the populated one."""
    assert deadline_bound(StopScope(), {}, now=_T0) == pytest.approx(BARRIER_SLACK_S)


def _scope_of(node_id: str, at: datetime | None = None) -> StopScope:
    scope = StopScope()
    scope.enter(node_id, _T0 if at is None else at)
    return scope


_T0 = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
"""The instant every arm's nodes started, so an arm states elapsed time as one offset."""


def _at(offset_s: float) -> datetime:
    """``offset_s`` seconds after :data:`_T0`."""
    return _T0 + timedelta(seconds=offset_s)
