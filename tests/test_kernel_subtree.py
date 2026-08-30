"""The subtree stop scope: who is being asked to stop, and who has already left.

Task 34 step 1. The scope is read from another task's coroutine on every matched tool
use, so what these arms pin is membership over time rather than a snapshot: a node that
has already gone terminal must not be notified, and a node that starts AFTER the stop was
requested must be.
"""

from __future__ import annotations

import asyncio

import pytest

from agentdag.application.kernel.subtree import BARRIER_SLACK_S, StopScope, barrier, deadline_bound
from agentdag.domain.models import NodeStatus


def test_request_stop_covers_every_in_flight_node_and_nothing_else() -> None:
    """Only nodes still in flight are notified; one that already landed is not."""
    scope = StopScope()
    scope.enter("a")
    scope.enter("b")
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
    scope.enter("late")

    assert scope.is_stopping("late")


def test_a_node_that_never_entered_is_not_stopping() -> None:
    """The predicate answers for THIS subtree only.

    Without this, an executor sharing one scope across sibling subtrees would notify a
    node that belongs to a different parent. Absence is a real answer, not a default.
    """
    scope = StopScope()
    scope.enter("mine")
    scope.request_stop()

    assert not scope.is_stopping("someone-elses")


def test_in_flight_tracks_entry_and_exit() -> None:
    """``in_flight`` is what the barrier waits on, so it must shrink as nodes land."""
    scope = StopScope()
    scope.enter("a")
    scope.enter("b")
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
    scope.enter("a")
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
    scope.enter("a")
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
    scope.enter("stuck")
    scope.request_stop()

    assert asyncio.run(barrier(scope, deadline_bound_s=0.05)) == frozenset({"stuck"})


def test_the_barrier_does_not_cancel_the_node_it_gave_up_on() -> None:
    """Design constraint 2: a stopped node is not killed. After a timeout the node must
    still be running - if the barrier cancelled it, its handover would be lost, which is
    the whole reason the notice precedes the drain."""
    scope = StopScope()
    scope.enter("stuck")
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
    short = deadline_bound(_scope_of("s"), {"s": _Spec(60.0)})
    long_ = deadline_bound(_scope_of("l"), {"l": _Spec(3600.0)})

    assert long_ - short == pytest.approx(3540.0, abs=1.0)


def test_the_bound_takes_the_largest_deadline_among_the_in_flight_nodes() -> None:
    """A subtree waits for its SLOWEST node, so the max governs, not the first or the sum."""
    scope = StopScope()
    scope.enter("fast")
    scope.enter("slow")
    graph = {"fast": _Spec(10.0), "slow": _Spec(900.0)}

    assert deadline_bound(scope, graph) == pytest.approx(900.0 + BARRIER_SLACK_S)


def test_a_node_with_no_deadline_falls_back_to_the_policy_ceiling() -> None:
    """`deadline_s` is None for a node that declares none. Treating that as 0.0 would make
    the barrier give up instantly and report it stuck, which reads as a deadline-enforcement
    failure when it is just an undeclared deadline.

    The fallback is the EXISTING `Policy.deadline_ceiling_s`, not a new knob: Checkpoint B
    required the bound be derived, and a node with no deadline of its own is bounded by the
    ceiling that clamps every node anyway."""
    scope = StopScope()
    scope.enter("undeclared")

    assert deadline_bound(scope, {"undeclared": _Spec(None)}, ceiling_s=1200.0) == pytest.approx(
        1200.0 + BARRIER_SLACK_S
    )


def test_an_undeclared_deadline_with_no_ceiling_is_reported_not_invented() -> None:
    """With neither a node deadline nor a ceiling there is nothing to derive from. The
    barrier must not invent a number: it says so, so the caller cannot mistake a guess for
    a bound."""
    scope = StopScope()
    scope.enter("undeclared")

    with pytest.raises(ValueError, match="no deadline"):
        deadline_bound(scope, {"undeclared": _Spec(None)})


def test_the_bound_of_an_empty_scope_is_the_slack_alone() -> None:
    """Nothing in flight means nothing to wait for. Decided deliberately rather than
    inherited: the empty case is not vacuously the same as the populated one."""
    assert deadline_bound(StopScope(), {}) == pytest.approx(BARRIER_SLACK_S)


def _scope_of(node_id: str) -> StopScope:
    scope = StopScope()
    scope.enter(node_id)
    return scope
