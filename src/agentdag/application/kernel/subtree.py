"""Which nodes a subtree is asking to stop, and waiting for them to finish.

Design constraint 2: a stopped node is NOT killed. The trigger puts a notice in front of
the model and then WAITS; the node hands over and its work stays evidence. Nothing here
cancels a task, and the barrier reports a node it could not wait out rather than
pretending it finished.

The scope is written by the execute loop and read from inside another task's coroutine,
on every matched tool use, so :meth:`StopScope.is_stopping` must be cheap and must never
block.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # annotations only: `from __future__ import annotations` defers them
    from collections.abc import Mapping
    from datetime import datetime

    from ...domain.models import NodeStatus

__all__ = ["BARRIER_SLACK_S", "StopScope", "barrier", "deadline_bound"]


class StopScope:
    """Which nodes are being asked to stop, when each started, and who has already left.

    One object per subtree. Stopping is a property of the SUBTREE and membership is a
    property of the NODE, which is why a node entering after :meth:`request_stop` is
    already stopping: the alternative freezes the membership list at the moment of the
    call, and a node launched a moment later would run unnotified while the barrier waited
    out its whole deadline.

    :meth:`enter` takes the instant the node's deadline began rather than reading a clock,
    so membership and that instant are recorded in ONE call and cannot diverge - a caller
    cannot enter a node and forget to stamp it, which is the omission
    :func:`deadline_bound` would otherwise absorb silently by falling back to the whole
    deadline. No clock lives here: this object is read on every matched tool use and must
    stay cheap and never block.

    Examples:
        >>> from datetime import datetime, timezone
        >>> t0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        >>> scope = StopScope()
        >>> scope.enter("a", t0)
        >>> scope.request_stop() == frozenset({"a"})
        True
        >>> scope.enter("late", t0)
        >>> scope.is_stopping("late")
        True
    """

    def __init__(self) -> None:
        """Start empty and not stopping."""
        self._in_flight: dict[str, datetime] = {}
        self._stopping = False

    def enter(self, node_id: str, at: datetime) -> None:
        """Record that ``node_id`` started at ``at`` and is now the barrier's to wait for.

        Args:
            node_id: The node entering this subtree.
            at: When this node's DEADLINE began - one reading of the ``Clock`` port, taken
                by the caller. It must be the instant the deadline is measured from,
                because the executor compares ``clock.now() - dispatch_started`` against
                ``deadline_s``
                (:meth:`~agentdag.adapters.kernel.executor_claude.ClaudeExecutor._deadline_exceeded`)
                and :func:`deadline_bound` has to agree with that. Stamping when the node
                was QUEUED rather than when it began running would count the wait for a
                parallel-bound slot against the node and under-estimate the bound.
        """
        self._in_flight[node_id] = at

    def leave(self, node_id: str, status: NodeStatus) -> None:
        """Record that ``node_id`` reached ``status`` and is no longer in flight.

        The status is taken but not branched on: every terminal status ends the wait
        equally, and a stopped node that fails is as finished as one that succeeds. It is
        in the signature because the caller has it and a later reader will ask whether the
        barrier distinguished them. It does not.
        """
        del status  # every terminal status ends the wait equally
        self._in_flight.pop(node_id, None)

    def request_stop(self) -> frozenset[str]:
        """Ask the whole subtree to stop and return the ids notified, for the journal."""
        self._stopping = True
        return frozenset(self._in_flight)

    def is_stopping(self, node_id: str) -> bool:
        """Whether ``node_id`` should hand over now.

        False for a node that never entered this scope, so a scope shared across sibling
        subtrees cannot notify someone else's node.
        """
        return self._stopping and node_id in self._in_flight

    def in_flight(self) -> frozenset[str]:
        """The ids that have entered and not yet left - what :func:`barrier` waits on."""
        return frozenset(self._in_flight)

    def in_flight_since(self) -> Mapping[str, datetime]:
        """Each in-flight id with the instant its deadline began, as a snapshot.

        A snapshot rather than the live dict, so a caller iterating it cannot be tripped by
        a node landing mid-iteration - the same reason the execute loop takes a snapshot of
        the graph before dispatching.
        """
        return dict(self._in_flight)


BARRIER_SLACK_S: float = 30.0
"""Room past the last deadline for the interrupt to land and the record to be written.

Nothing rests on the exact value: it is slack on a bound that is already correct, not the
bound itself.
"""

_POLL_S: float = 0.01
"""How often the barrier re-checks. An implementation detail, not a knob: it bounds how
long past termination the barrier lingers, never how long it waits."""


class _HasDeadline(Protocol):
    """The one field :func:`deadline_bound` reads off a spec.

    Declared as a Protocol rather than taking ``NodeSpec`` so the bound is honestly typed
    by what it uses. A caller passing a full ``NodeSpec`` satisfies it structurally.
    """

    @property
    def deadline_s(self) -> float | None:
        """Wall-clock seconds this node may run for, or None if it declares none."""


def deadline_bound(
    scope: StopScope,
    graph: Mapping[str, _HasDeadline],
    *,
    now: datetime,
    ceiling_s: float | None = None,
) -> float:
    """Largest REMAINING deadline among the in-flight nodes, plus :data:`BARRIER_SLACK_S`.

    DECIDED at Checkpoint B (user, 2026-08-29): the bound is DERIVED, never a new knob.
    Task 21 already enforces every node's deadline in wall clock, so a node outliving this
    bound does not mean the barrier was impatient - it means deadline enforcement itself
    failed, which is precisely what the barrier's returned set should report.

    Each node contributes the time it has LEFT, not the deadline it was given (user,
    2026-08-30). Taking the whole deadline looks free because the wait ends as soon as the
    last node lands - but that is the case the barrier is NOT for. In the case it IS for,
    enforcement having failed, the wait runs to the full bound, so a node 3599s into a
    3600s deadline would be reported stuck ~3630s later instead of ~31s. Rejected in the
    same breath: a fixed maximum cap, which is the new knob Checkpoint B ruled out.

    Start times come from the scope, which took each one in the same call that entered the
    node (:meth:`StopScope.enter`), so an in-flight node without a start cannot exist and
    there is no absent case to decide here. No clock is read: the execute loop holds the
    ``Clock`` port (``ctx.co.clock``) and passes one reading in as ``now``.

    Args:
        scope: The subtree whose in-flight nodes bound the wait, and where each of them
            started.
        graph: Specs by node id, for the in-flight nodes' declared deadlines.
        now: One reading of the ``Clock`` port, shared by every node in this call so they
            are all measured against the same instant.
        ceiling_s: ``Policy.deadline_ceiling_s``, used for a node declaring no deadline of
            its own. An existing policy value rather than a new knob - the ceiling already
            clamps every node - and it counts down from the node's start like a declared
            deadline, because it is standing in for one.

    Returns:
        Seconds to wait at most. The slack alone when nothing is in flight, and the slack
        alone when every in-flight node is already past its deadline.

    Raises:
        ValueError: A node declares no deadline and no ceiling was given, so there is
            nothing to derive a bound from. Reported rather than invented, so a caller
            cannot mistake a guess for a bound.

    Examples:
        >>> from datetime import datetime, timezone
        >>> t0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        >>> deadline_bound(StopScope(), {}, now=t0) == BARRIER_SLACK_S
        True
    """
    longest = 0.0
    for node_id, started in scope.in_flight_since().items():
        spec = graph.get(node_id)
        declared = None if spec is None else spec.deadline_s
        effective = declared if declared is not None else ceiling_s
        if effective is None:
            raise ValueError(f"node {node_id!r} declares no deadline and no ceiling was given")
        longest = max(longest, _remaining_s(effective, started, now))
    return longest + BARRIER_SLACK_S


def _remaining_s(deadline_s: float, started: datetime, now: datetime) -> float:
    """How much of ``deadline_s`` is left for a node started at ``started``.

    Clamped at BOTH ends, each for its own reason. Elapsed floors at zero so a wall clock
    that stepped backwards under NTP cannot hand back more time than the node was ever
    given. The remainder floors at zero because this returns a DURATION and a negative one
    is not a duration: :func:`deadline_bound` would absorb it anyway - its accumulator
    starts at ``0.0``, so an overdue node cannot pull the bound below the slack whether
    this clamps or not - so the floor is stated here as this helper's own contract, for
    the next caller rather than for the one that exists.

    Args:
        deadline_s: The node's whole deadline, or the ceiling standing in for it.
        started: When that deadline began, from :meth:`StopScope.in_flight_since`.
        now: The shared clock reading to measure against.

    Returns:
        Seconds left, in ``[0.0, deadline_s]``.

    Examples:
        >>> from datetime import datetime, timedelta, timezone
        >>> t0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        >>> _remaining_s(60.0, t0, t0 + timedelta(seconds=45))
        15.0
        >>> _remaining_s(60.0, t0, t0 + timedelta(seconds=600))
        0.0
    """
    elapsed_s = max(0.0, (now - started).total_seconds())
    return max(0.0, deadline_s - elapsed_s)


async def barrier(scope: StopScope, *, deadline_bound_s: float) -> frozenset[str]:
    """Wait until every node in ``scope`` is terminal.

    Returns the ids still in flight when the bound ran out; EMPTY is the success case.

    Never cancels anything (design constraint 2: a stopped node is not killed). A node
    still running at the bound is REPORTED, because returning success on a timeout would
    let Task 35 re-plan around a node still writing to the worktree, which is the exact
    race this exists to prevent.

    Args:
        scope: The subtree to wait on.
        deadline_bound_s: Ceiling on the wait, from :func:`deadline_bound`.

    Returns:
        The ids still in flight at the bound; empty when every node went terminal.

    Examples:
        >>> import asyncio
        >>> asyncio.run(barrier(StopScope(), deadline_bound_s=0.0)) == frozenset()
        True
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_bound_s
    while True:
        remaining = scope.in_flight()
        if not remaining:
            return frozenset()
        if loop.time() >= deadline:
            return remaining
        await asyncio.sleep(_POLL_S)
