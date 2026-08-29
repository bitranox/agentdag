"""The recursive execute loop: run one plan's entries to terminal, recursing on sub-plans.

M6 component 3, and design section 3.3's scheduler in code. One plan's entries are dispatched
in waves - everything whose ``deps`` are satisfied goes at once, bounded by the run-wide
semaphore - and each landed record is put through the design's TWO condition checks: the
entry's own ``acceptance``, and the plan-wide ``holds_while``. A ``plan`` entry is
SPECIAL-CASED into a recursion rather than dispatched through the registry, which is why the
registry's ``plan`` body is a guard that raises
(:func:`~agentdag.composition.kernel.build_op_registry`).

What this module deliberately does NOT do, and where it is owed:

* **It never re-plans.** A refuted condition comes back in :attr:`Executed.fired` and the
  subtree stops; the stop notice, the barrier and the re-dispatch are Tasks 34 and 35. A
  loop that re-planned here would do it while the wave's other nodes were still in flight,
  which is exactly what the barrier exists to prevent.
* **It stops the subtree between WAVES, not inside one.** Every entry dispatched in a wave
  runs to terminal; a refutation stops the NEXT wave from starting. Interrupting a node
  already in flight needs the stop notice, which does not exist yet - so the honest
  statement of what "stops the subtree" means today is "starts nothing further".
* **It does not read ``spec.isolation``.** Where an entry runs is component 8's subject, and
  ``Isolation`` remains parsed-never-enforced (Checkpoint A finding 4, "own worktree").
  Every entry runs in ``ctx.cwd``.

Contents:
    * :class:`RunNodeBudgetExceededError` / :class:`PlanDepthExceededError` - the two run bounds' errors.
    * :class:`NodeBudget` - how many nodes this RUN has dispatched, shared across every plan.
    * :class:`Executed` - one subtree's records, whether it is done, and what refuted it.
    * :func:`execute_plan` - the loop itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...domain.condition import evaluate
from ...domain.kernel_errors import KernelError
from ...domain.models import ResultRecord
from ...domain.plan import evaluate_holds_while
from .planner import Planned, dispatch_planner

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from ...domain.condition import Condition
    from ...domain.models import NodeSpec
    from ...domain.plan import Entry, Plan
    from ...domain.policy import RunLimits
    from .registry import OpRegistry, PlanContext

__all__ = [
    "Executed",
    "NodeBudget",
    "PlanDepthExceededError",
    "RunNodeBudgetExceededError",
    "execute_plan",
]

PLAN_OP = "plan"
"""The one op name the loop special-cases into a recursion instead of dispatching.

Spelled once, here, and matched against :attr:`~agentdag.domain.plan.Entry.op` - the same
literal :func:`~agentdag.composition.kernel.build_op_registry` registers the guard body
under. Design section 3.3's scheduler branches on exactly this name.
"""


class RunNodeBudgetExceededError(KernelError):
    """The run tried to dispatch more nodes than ``max_nodes_per_run`` allows."""


class PlanDepthExceededError(KernelError):
    """A nested plan went deeper than ``max_plan_depth`` allows (Checkpoint B, decided)."""


class NodeBudget:
    """How many nodes this RUN has dispatched, shared across every plan and recursion.

    Mutable and passed down deliberately: a frozen count would have to be threaded back up
    through every recursion, and the one thing this must not be is per-plan. A sub-plan
    spends from the same total as the plan that asked for it.

    It holds no ceiling of its own. :meth:`spend` takes the limit as a REQUIRED keyword, so
    the one value in :class:`~agentdag.domain.policy.RunLimits` stays the only source of
    truth and a caller cannot spend without deciding what the bound is.
    """

    def __init__(self) -> None:
        """Start at zero."""
        self._spent = 0

    def spend(self, n: int = 1, *, limit: int) -> None:
        """Charge ``n`` dispatches against the run, refusing to cross ``limit``.

        Charged BEFORE the dispatch it pays for, so the refusal arrives instead of the
        spend rather than after it.

        Args:
            n: How many node dispatches to charge.
            limit: The run's ``max_nodes_per_run``.

        Raises:
            RunNodeBudgetExceededError: the charge would take the run past ``limit``.
        """
        if self._spent + n > limit:
            raise RunNodeBudgetExceededError(
                f"the run has dispatched {self._spent} nodes and this call needs {n} more, "
                f"which crosses max_nodes_per_run={limit}"
            )
        self._spent += n

    @property
    def spent(self) -> int:
        """How many node dispatches this run has charged."""
        return self._spent


@dataclass(frozen=True, slots=True)
class Executed:
    """What running one plan's subtree produced."""

    records: Mapping[str, ResultRecord]
    """Every terminal record of this subtree, by node id - a nested sub-plan's own records
    joined in, so a parent reads one flat mapping however deep the recursion went."""

    done: bool
    """Whether ``plan.done_when`` settled TRUE. An UNDECIDED ``done_when`` is not done: a
    completion condition that cannot be settled has not been met."""

    fired: Condition | None
    """The condition that was REFUTED, if one was: an entry's ``acceptance`` or the plan's
    ``holds_while``. ``None`` when nothing refuted - which includes every undecided
    condition, because three-valued means an absent field is not a failure."""

    fired_on: str | None
    """The node id whose landed record refuted :attr:`fired`. For a ``holds_while`` that is
    the record that LANDED, not the guard's owner: the entry may itself have passed."""


@dataclass(frozen=True, slots=True)
class _Landed:
    """One entry's dispatch, and everything it contributed to the parent's records."""

    entry: Entry
    record: ResultRecord
    """The entry's OWN record - what its ``acceptance`` is evaluated against. For a ``plan``
    entry this is the PLANNER node's record, the only record the entry itself produces."""

    subtree: Mapping[str, ResultRecord]
    """A sub-plan's records, empty for every op but ``plan``."""


@dataclass(frozen=True, slots=True)
class _Refutation:
    """A condition that came back False, and the record that settled it."""

    condition: Condition
    node_id: str


async def execute_plan(
    plan: Plan,
    *,
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    depth: int,
    spent: NodeBudget,
    admitted: Mapping[str, NodeSpec] | None = None,
) -> Executed:
    """Run one plan's entries to terminal, recursing on ``op="plan"`` entries.

    Returns rather than re-plans: a refuted condition is REPORTED in
    :attr:`Executed.fired` and the subtree starts nothing further. The
    trigger/barrier/re-dispatch path is Tasks 34 and 35.

    Args:
        plan: The validated plan to run. Already through
            :func:`~agentdag.application.kernel.plan_validate.validate_plan`, so its ops are
            registered, its ids allocated and its conditions within contract.
        ctx: The coordinator to dispatch through, and the directory entries run in.
        registry: The ops an entry may name.
        limits: The run's ceilings; ``max_nodes_per_run`` and ``max_plan_depth`` bind here.
        depth: How deep this plan sits, 0 for the run's own top-level plan.
        spent: The RUN's node budget, shared with every other plan of this run.
        admitted: The nodes admitted ABOVE this plan, by node id - what a sub-plan's deps
            and conditions may name besides its own entries
            (:func:`~agentdag.application.kernel.plan_validate.validate_plan`'s ``graph``).
            ``None`` at the root, where nothing has been admitted yet. Threaded rather than
            read off the coordinator because the dispatcher keeps RECORDS, not specs, so
            this loop is the only place the specs of already-run entries exist.

    Returns:
        This subtree's records, whether ``done_when`` settled true, and what refuted, if
        anything did.

    Raises:
        PlanDepthExceededError: ``depth`` has reached ``limits.max_plan_depth``.
        RunNodeBudgetExceededError: a dispatch would cross ``limits.max_nodes_per_run``.
    """
    _refuse_too_deep(depth, limits)
    records: dict[str, ResultRecord] = {}
    graph: dict[str, NodeSpec] = dict(admitted or {})
    pending = {e.spec.node_id: e for e in plan.entries}
    refutation: _Refutation | None = None
    while pending and refutation is None:
        ready = _ready(pending, records)
        if not ready:
            break
        for node_id in [e.spec.node_id for e in ready]:
            del pending[node_id]
        landed = await _run_wave(
            ready, ctx=ctx, registry=registry, limits=limits, depth=depth, spent=spent, admitted=graph
        )
        _absorb(landed, into=records)
        # AFTER the wave, never during it: a sub-plan dispatched by this wave may name a node
        # admitted by an EARLIER wave, but not its own wave-mates - entries ready together are
        # by definition entries neither depends on.
        graph.update({item.entry.spec.node_id: item.entry.spec for item in landed})
        refutation = _first_refutation(landed, plan=plan, view=_view(ctx, records))
    return _executed(plan, records=records, view=_view(ctx, records), refutation=refutation)


def _refuse_too_deep(depth: int, limits: RunLimits) -> None:
    """Refuse a plan nested at or past ``max_plan_depth``, before it dispatches anything.

    Checked at the top of the loop, which is the ONE place the rule lives: a second check at
    the recursion site would save one planner dispatch per runaway and duplicate the
    invariant, and a duplicated bound is one that can drift.
    """
    if depth >= limits.max_plan_depth:
        raise PlanDepthExceededError(
            f"a nested plan reached depth {depth}, at or past max_plan_depth={limits.max_plan_depth}"
        )


def _ready(pending: Mapping[str, Entry], records: Mapping[str, ResultRecord]) -> list[Entry]:
    """Return the pending entries whose deps have all landed, in plan order.

    A dep naming something that is NOT an entry of this plan is already satisfied: the
    validator only admits an outside dep that names an already-admitted node
    (:func:`~agentdag.application.kernel.plan_validate.validate_plan`'s dep rule), and an
    admitted node is terminal before this plan started. Waiting for one would deadlock the
    plan on a record this loop is never going to produce.
    """
    return [e for e in pending.values() if all(dep not in pending or dep in records for dep in e.spec.deps)]


async def _run_wave(
    ready: Sequence[Entry],
    *,
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    depth: int,
    spent: NodeBudget,
    admitted: Mapping[str, NodeSpec],
) -> list[_Landed]:
    """Dispatch every entry of one wave concurrently and return them in wave order.

    ``return_exceptions=True`` then re-raise, rather than letting ``gather`` cancel: a
    sibling already in flight is a REAL node with a real cost, and cancelling it to report
    an error raised by another branch would abandon work the journal has no record of.
    Every branch is allowed to finish, then the first failure is raised.
    """
    outcomes = await asyncio.gather(
        *(
            _run_entry(e, ctx=ctx, registry=registry, limits=limits, depth=depth, spent=spent, admitted=admitted)
            for e in ready
        ),
        return_exceptions=True,
    )
    return [_landed_or_raise(outcome) for outcome in outcomes]


def _landed_or_raise(outcome: _Landed | BaseException) -> _Landed:
    """Return a wave branch's result, re-raising whatever it raised instead.

    ``BaseException`` and not ``Exception``: ``gather(return_exceptions=True)`` collects a
    ``CancelledError`` or a ``KeyboardInterrupt`` alongside ordinary failures, and those are
    the coordinator process itself going away - the same rule
    :meth:`~agentdag.application.kernel.context.Coordinator.map` states for its own branches.
    """
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


async def _run_entry(
    entry: Entry,
    *,
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    depth: int,
    spent: NodeBudget,
    admitted: Mapping[str, NodeSpec],
) -> _Landed:
    """Dispatch one entry, recursing when it names ``plan``."""
    spent.spend(limit=limits.max_nodes_per_run)
    if entry.op == PLAN_OP:
        return await _run_sub_plan(
            entry, ctx=ctx, registry=registry, limits=limits, depth=depth, spent=spent, admitted=admitted
        )
    record = await _dispatch_leaf(entry, ctx=ctx, registry=registry)
    return _Landed(entry=entry, record=record, subtree={})


async def _dispatch_leaf(entry: Entry, *, ctx: PlanContext, registry: OpRegistry) -> ResultRecord:
    """Build this entry's op body and await it inside one slot of the run-wide bound.

    The slot is held around the body ALONE. A recursion must never hold one (see
    :func:`_run_sub_plan`), and neither may anything else that waits on a nested dispatch.
    """
    body = registry.get(entry.op).build(entry, ctx)
    async with ctx.co.parallel_bound():
        result = await body()
    return _record_of(result, entry=entry, ctx=ctx)


def _record_of(result: object, *, entry: Entry, ctx: PlanContext) -> ResultRecord:
    """Return the record a body produced, resolving ``approve``'s :class:`Decision` to one.

    :data:`~agentdag.application.kernel.registry.Body` is typed to return a
    ``ResultRecord`` OR a ``Decision``, and ``approve`` is the op that returns the latter -
    but it dispatches internally all the same, so its record is on the dispatcher. Reading
    it back from there is how ``reduce:count``'s own fold already reaches the run's records
    (:func:`~agentdag.composition.kernel.build_op_registry`), not a new coupling.

    Raises:
        KernelError: the body returned a ``Decision`` and no record was recorded for the
            node - which would mean the primitive returned without dispatching, and a
            condition over this entry could never be settled.
    """
    if isinstance(result, ResultRecord):
        return result
    record = ctx.co.dispatcher.records.get(entry.spec.node_id)
    if record is None:
        raise KernelError(f"entry {entry.spec.node_id!r} (op {entry.op!r}) returned a decision but recorded no result")
    return record


async def _run_sub_plan(
    entry: Entry,
    *,
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    depth: int,
    spent: NodeBudget,
    admitted: Mapping[str, NodeSpec],
) -> _Landed:
    """Dispatch this entry's planner, then execute what it planned one level deeper.

    The run-wide slot is taken for the PLANNER DISPATCH ONLY and released before the
    recursion: holding it across ``execute_plan`` would deadlock at ``parallel=1``, because
    the sub-plan's own leaves queue for the same semaphore the parent is sitting on.

    A sub-plan that will not validate comes back as
    :class:`~agentdag.application.kernel.planner.NotPlanned`. Its planner record still joins
    the parent's records, and the entry contributes no subtree - so the entry's dependents
    never become ready and this subtree stops with ``done`` false. Turning those reasons
    into a re-dispatch is Task 35's; reporting them as a stalled subtree is what this loop
    can honestly do without a barrier.
    """
    async with ctx.co.parallel_bound():
        planned = await dispatch_planner(
            spec=entry.spec,
            goal=_sub_goal(entry),
            evidence=_evidence(entry, ctx),
            ctx=ctx,
            registry=registry,
            limits=limits,
            graph=admitted,
            is_root=False,
            allocate_id=_allocator(ctx),
        )
    if not isinstance(planned, Planned):
        return _Landed(entry=entry, record=planned.record, subtree={})
    sub = await execute_plan(
        planned.plan,
        ctx=ctx,
        registry=registry,
        limits=limits,
        depth=depth + 1,
        spent=spent,
        admitted=admitted,
    )
    return _Landed(entry=entry, record=planned.record, subtree=sub.records)


def _sub_goal(entry: Entry) -> str:
    """Return the sub-goal a ``plan`` entry asks for.

    Read straight off ``args``, which the op's own args model already made a required
    string (``_PlanArgs`` in :mod:`agentdag.composition.kernel`), so a plan that reached
    here without one was refused at plan-accept time.

    Raises:
        KernelError: the entry carries no ``goal`` - a hand-built entry that never went
            through the validator.
    """
    goal = entry.args.get("goal")
    if not isinstance(goal, str):
        raise KernelError(f"plan entry {entry.spec.node_id!r} carries no string 'goal' in its args")
    return goal


def _evidence(entry: Entry, ctx: PlanContext) -> dict[str, ResultRecord]:
    """Return the records this ``plan`` entry's own deps produced, for the planner's brief."""
    records = ctx.co.dispatcher.records
    return {dep: records[dep] for dep in entry.spec.deps if dep in records}


def _allocator(ctx: PlanContext) -> Callable[[], str]:
    """Hand out node ids for a sub-plan's entries, unique within this run.

    Keyed on how many records the run already holds, so two sub-plans of one run cannot mint
    the same id: an id already dispatched is in ``dispatcher.records``, and the counter walks
    past any that collide rather than trusting its own arithmetic.
    """
    taken = ctx.co.dispatcher.records
    counter = {"n": 0}

    def allocate() -> str:
        while True:
            counter["n"] += 1
            candidate = f"n-{counter['n']:04d}"
            if candidate not in taken:
                return candidate

    return allocate


def _absorb(landed: Iterable[_Landed], *, into: dict[str, ResultRecord]) -> None:
    """Merge a wave's records - each entry's own, plus any subtree it expanded into."""
    for item in landed:
        into[item.entry.spec.node_id] = item.record
        into.update(item.subtree)


def _view(ctx: PlanContext, records: Mapping[str, ResultRecord]) -> Mapping[str, ResultRecord]:
    """Return the records a condition of this plan may name.

    The RUN's records, not only this subtree's: the validator admits a ``FieldRef`` naming
    an already-admitted node outside this plan (``plan_validate._field_ref_reasons``), so a
    view narrowed to this subtree would read those as undecided forever. This subtree's own
    records are layered on top, which changes nothing today - every dispatch records on the
    dispatcher - and keeps the merge true if that ever narrows.
    """
    return {**ctx.co.dispatcher.records, **records}


def _first_refutation(landed: Sequence[_Landed], *, plan: Plan, view: Mapping[str, ResultRecord]) -> _Refutation | None:
    """Return the first condition a wave's records refuted, in wave order, or ``None``.

    Two checks per landed record, in design section 4's own order: the entry's own
    ``acceptance``, then the plan's ``holds_while``. Only a settled ``False`` refutes - an
    undecided condition is not a failure, which is the whole reason
    :func:`~agentdag.domain.condition.evaluate` is three-valued. A node merely FINISHING is
    not a trigger.
    """
    for item in landed:
        node_id = item.entry.spec.node_id
        acceptance = item.entry.acceptance
        if acceptance is not None and evaluate(acceptance, view) is False:
            return _Refutation(condition=acceptance, node_id=node_id)
        if plan.holds_while is not None and evaluate_holds_while(plan, view) is False:
            return _Refutation(condition=plan.holds_while, node_id=node_id)
    return None


def _executed(
    plan: Plan,
    *,
    records: Mapping[str, ResultRecord],
    view: Mapping[str, ResultRecord],
    refutation: _Refutation | None,
) -> Executed:
    """Settle ``done_when`` over what ran and assemble the subtree's result.

    A REFUTED subtree is never done, whatever ``done_when`` says. Design section 3.3
    evaluates ``done_when`` only after the loop runs to completion; a refutation leaves it
    through ``replan``, so the two verdicts never meet there. They can meet here, and the
    refutation wins: a plan that stopped early has entries that never ran, so a
    ``done_when`` settling True over the records that DID land is answering a question about
    a plan nobody finished. Reporting ``done`` beside a non-null ``fired`` would also hand
    Task 35 a contradiction to branch on.
    """
    return Executed(
        records=records,
        done=refutation is None and evaluate(plan.done_when, view) is True,
        fired=None if refutation is None else refutation.condition,
        fired_on=None if refutation is None else refutation.node_id,
    )
