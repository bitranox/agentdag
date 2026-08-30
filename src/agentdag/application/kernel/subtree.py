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

    from ...domain.models import NodeStatus

__all__ = ["BARRIER_SLACK_S", "StopScope", "barrier", "deadline_bound"]


class StopScope:
    """Which nodes are being asked to stop, readable from another task's coroutine.

    One object per subtree. Stopping is a property of the SUBTREE and membership is a
    property of the NODE, which is why a node entering after :meth:`request_stop` is
    already stopping: the alternative freezes the membership list at the moment of the
    call, and a node launched a moment later would run unnotified while the barrier waited
    out its whole deadline.

    Examples:
        >>> scope = StopScope()
        >>> scope.enter("a")
        >>> scope.request_stop() == frozenset({"a"})
        True
        >>> scope.enter("late")
        >>> scope.is_stopping("late")
        True
    """

    def __init__(self) -> None:
        """Start empty and not stopping."""
        self._in_flight: set[str] = set()
        self._stopping = False

    def enter(self, node_id: str) -> None:
        """Record that ``node_id`` has started and is now the barrier's to wait for."""
        self._in_flight.add(node_id)

    def leave(self, node_id: str, status: NodeStatus) -> None:
        """Record that ``node_id`` reached ``status`` and is no longer in flight.

        The status is taken but not branched on: every terminal status ends the wait
        equally, and a stopped node that fails is as finished as one that succeeds. It is
        in the signature because the caller has it and a later reader will ask whether the
        barrier distinguished them. It does not.
        """
        del status  # every terminal status ends the wait equally
        self._in_flight.discard(node_id)

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
        """The ids that have entered and not yet left."""
        return frozenset(self._in_flight)


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
    ceiling_s: float | None = None,
) -> float:
    """Largest deadline among the in-flight nodes, plus :data:`BARRIER_SLACK_S`.

    DECIDED at Checkpoint B (user, 2026-08-29): the bound is DERIVED, never a new knob.
    Task 21 already enforces every node's deadline in wall clock, so a node outliving this
    bound does not mean the barrier was impatient - it means deadline enforcement itself
    failed, which is precisely what the barrier's returned set should report.

    This takes each node's WHOLE deadline rather than its remaining time. The plan said
    "remaining", which would need a start time per node and therefore a clock inside an
    object read on every matched tool use. It buys nothing: the bound is a CEILING on a
    wait that ends as soon as the last node lands, so over-estimating it costs nothing in
    the normal case and only delays reporting a genuine deadline-enforcement failure.
    Under-estimating it would fail subtrees whose nodes were about to terminate normally,
    which is the error that matters.

    Args:
        scope: The subtree whose in-flight nodes bound the wait.
        graph: Specs by node id, for the in-flight nodes' declared deadlines.
        ceiling_s: ``Policy.deadline_ceiling_s``, used for a node declaring no deadline of
            its own. An existing policy value rather than a new knob - the ceiling already
            clamps every node.

    Returns:
        Seconds to wait at most. The slack alone when nothing is in flight.

    Raises:
        ValueError: A node declares no deadline and no ceiling was given, so there is
            nothing to derive a bound from. Reported rather than invented, so a caller
            cannot mistake a guess for a bound.

    Examples:
        >>> deadline_bound(StopScope(), {}) == BARRIER_SLACK_S
        True
    """
    longest = 0.0
    for node_id in scope.in_flight():
        spec = graph.get(node_id)
        declared = None if spec is None else spec.deadline_s
        effective = declared if declared is not None else ceiling_s
        if effective is None:
            raise ValueError(f"node {node_id!r} declares no deadline and no ceiling was given")
        longest = max(longest, effective)
    return longest + BARRIER_SLACK_S


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
