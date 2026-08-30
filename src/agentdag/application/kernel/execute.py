"""The recursive execute loop: run one plan's entries to terminal, recursing on sub-plans.

M6 component 3, and design section 3.3's scheduler in code. Entries are dispatched as soon as
their ``deps`` are satisfied, bounded by the run-wide semaphore, and CONTINUOUSLY: the loop
waits for the next record to land - never for a whole batch - so an entry whose own dep has
landed starts at once rather than behind the slowest of its peers. Each landed record goes
through the design's TWO condition checks: the entry's own ``acceptance``, and the plan-wide
``holds_while``. A ``plan`` entry is SPECIAL-CASED into a recursion rather than dispatched
through the registry, which is why the registry's ``plan`` body is a guard that raises
(:func:`~agentdag.composition.kernel.build_op_registry`).

What this module deliberately does NOT do, and where it is owed:

* **It never re-plans.** A refuted condition, and a sub-plan the validator refused, both come
  back on :class:`Executed` and the subtree starts nothing further; the stop notice and the
  re-dispatch are Tasks 34 and 35. What this loop DOES do at that moment is wait for the nodes
  already in flight to reach terminal, because abandoning them would leave real dispatches
  unawaited - a barrier without the notice that should precede it.
* **It cannot stop a node already running.** Interrupting one needs the stop notice, which does
  not exist yet, so "stops the subtree" means "starts nothing further and waits out what is
  running".
* **It does not read ``spec.isolation``.** Where an entry runs is component 8's subject (user,
  2026-08-30); ``Isolation`` remains parsed-never-enforced. Every entry runs in ``ctx.cwd``.

Contents:
    * :class:`RunNodeBudgetExceededError` / :class:`PlanDepthExceededError` - the run bounds' errors.
    * :class:`NodeBudget` - how many nodes this RUN has dispatched, shared across every plan.
    * :class:`NodeIds` - the RUN's node-id allocator, shared for the same reason.
    * :class:`SubPlanRefused` - one sub-plan the validator refused, with its reasons verbatim.
    * :class:`Executed` - one subtree's records, whether it is done, and why it stopped.
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
    from collections.abc import Mapping, Sequence

    from ...domain.condition import Condition
    from ...domain.models import NodeSpec
    from ...domain.plan import Entry, Plan
    from ...domain.policy import RunLimits
    from .registry import OpRegistry, PlanContext

__all__ = [
    "Executed",
    "NodeBudget",
    "NodeIds",
    "PlanDepthExceededError",
    "RunNodeBudgetExceededError",
    "SubPlanRefused",
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


class NodeIds:
    """The RUN's node-id allocator: one counter for every plan and sub-plan of one run.

    Run-scoped for the same reason :class:`NodeBudget` is, and found the same way - by
    measurement rather than by argument. An earlier version built a counter per sub-plan and
    skipped ids already in ``dispatcher.records``; two ``plan`` entries recursing at the same
    time both minted ``n-0001``, because an id that has been ALLOCATED but not yet DISPATCHED
    is in no record yet. Two nodes then shared one id, one journal key and one node directory
    - exactly what
    :func:`~agentdag.application.kernel.plan_validate.validate_plan`'s duplicate-id rule
    refuses WITHIN a plan, reintroduced ACROSS plans.

    Ids are opaque and carry no plan structure (``n-0001``, not ``n-<parent>-0001``): the
    schemas, the ``hash8`` node directories and the design all treat a node id as a name, not
    a path.
    """

    def __init__(self) -> None:
        """Start before the first id."""
        self._n = 0

    def allocate(self) -> str:
        """Mint the next node id for this run."""
        self._n += 1
        return f"n-{self._n:04d}"

    @property
    def minted(self) -> int:
        """How many ids this run has handed out."""
        return self._n


@dataclass(frozen=True, slots=True)
class SubPlanRefused:
    """A ``plan`` entry whose planner produced nothing the validator would accept."""

    node_id: str
    """The ``plan`` entry's own node id - the planner node that was dispatched."""

    reasons: tuple[str, ...]
    """Why, VERBATIM: the validator's own reasons, or what went wrong reading what the node
    wrote. Carried unflattened all the way from
    :class:`~agentdag.application.kernel.planner.NotPlanned` because the planner re-dispatched
    for this subtree (Task 35) is briefed with them, and a summary cannot be acted on."""


@dataclass(frozen=True, slots=True)
class Executed:
    """What running one plan's subtree produced, and why it stopped."""

    records: Mapping[str, ResultRecord]
    """Every terminal record of this subtree, by node id - a nested sub-plan's own records
    joined in, so a parent reads one flat mapping however deep the recursion went."""

    done: bool
    """Whether ``plan.done_when`` settled TRUE and nothing refuted. An UNDECIDED ``done_when``
    is not done: a completion condition that cannot be settled has not been met."""

    fired: Condition | None
    """The condition that was REFUTED, if one was: an entry's ``acceptance`` or the plan's
    ``holds_while``. ``None`` when nothing refuted - which includes every undecided
    condition, because three-valued means an absent field is not a failure."""

    fired_on: str | None
    """The node id whose landed record refuted :attr:`fired`. For a ``holds_while`` that is
    the record that LANDED, not the guard's owner: the entry may itself have passed."""

    refused: tuple[SubPlanRefused, ...]
    """Every sub-plan of this subtree the validator refused, at any depth, in the order they
    landed. Empty when none was. A DIFFERENT cause from :attr:`fired` and kept apart from it
    on purpose: a refuted premise and a planner that emitted an invalid plan need different
    briefs when the planner is re-dispatched."""

    unrun: tuple[str, ...]
    """The entries of this subtree that were never dispatched, in plan order - because the
    subtree stopped, or because their deps never landed. Design section 4 step 4 is what
    needs this: "the new plan replaces S's UNEXECUTED entries", and this names them."""


@dataclass(frozen=True, slots=True)
class _Wiring:
    """What every level of one run's recursion shares, bundled so it is threaded once.

    ``spent`` and ``ids`` are the two RUN-scoped mutables; the rest is fixed for the run.
    Depth and the admitted-node graph are NOT here - they differ per level.
    """

    ctx: PlanContext
    registry: OpRegistry
    limits: RunLimits
    spent: NodeBudget
    ids: NodeIds


@dataclass(frozen=True, slots=True)
class _Landed:
    """One entry's dispatch, and everything it contributed to the parent."""

    entry: Entry
    record: ResultRecord
    """The entry's OWN record - what its ``acceptance`` is evaluated against. For a ``plan``
    entry this is the PLANNER node's record, the only record the entry itself produces."""

    subtree: Mapping[str, ResultRecord]
    """A sub-plan's records, empty for every op but ``plan``."""

    refused: tuple[SubPlanRefused, ...] = ()
    """Sub-plan refusals from this entry and anything below it."""

    unrun: tuple[str, ...] = ()
    """Entries a nested sub-plan never dispatched."""


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
    ids: NodeIds | None = None,
    admitted: Mapping[str, NodeSpec] | None = None,
) -> Executed:
    """Run one plan's entries to terminal, recursing on ``op="plan"`` entries.

    Returns rather than re-plans: a refuted condition and a refused sub-plan are both
    REPORTED on :class:`Executed`, and the subtree starts nothing further. The
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
        ids: The RUN's node-id allocator, shared for the same reason. ``None`` creates one,
            which is right only for a call that IS the run's top-level plan - two runs
            sharing a coordinator must not share ids, and two plans of ONE run must.
        admitted: The nodes admitted ABOVE this plan, by node id - what a sub-plan's deps and
            conditions may name besides its own entries (``validate_plan``'s ``graph``).
            ``None`` at the root. Threaded rather than read off the coordinator because the
            dispatcher keeps RECORDS, not specs, so this loop is the only place the specs of
            already-run entries exist.

    Returns:
        This subtree's records, whether it is done, and why it stopped.

    Raises:
        PlanDepthExceededError: ``depth`` has reached ``limits.max_plan_depth``.
        RunNodeBudgetExceededError: a dispatch would cross ``limits.max_nodes_per_run``.
    """
    wiring = _Wiring(ctx=ctx, registry=registry, limits=limits, spent=spent, ids=ids or NodeIds())
    return await _execute(plan, wiring=wiring, depth=depth, admitted=dict(admitted or {}))


async def _execute(plan: Plan, *, wiring: _Wiring, depth: int, admitted: dict[str, NodeSpec]) -> Executed:
    """Drive one plan to terminal. The recursion re-enters HERE, sharing ``wiring``."""
    _refuse_too_deep(depth, wiring.limits)
    run = _Progress(pending={e.spec.node_id: e for e in plan.entries}, graph=admitted)
    in_flight: dict[asyncio.Task[_Landed], Entry] = {}
    while run.refutation is None and run.failure is None:
        run.failure = _launch_ready(run, in_flight, wiring=wiring, depth=depth)
        if not in_flight or run.failure is not None:
            break
        await _settle(await _await_next(in_flight), run, plan=plan, wiring=wiring)
    await _settle(await _await_all(in_flight), run, plan=plan, wiring=wiring)
    if run.failure is not None:
        raise run.failure
    return _executed(plan, run=run, view=_view(wiring.ctx, run.records))


class _Progress:
    """One plan's mutable progress: what is left, what landed, and why it stopped.

    A class rather than six locals threaded through helpers - the loop's helpers all read and
    write the same set, and passing them individually is what turned the first version's
    signatures into six-parameter lists.
    """

    def __init__(self, *, pending: dict[str, Entry], graph: dict[str, NodeSpec]) -> None:
        """Start with every entry pending and nothing landed."""
        self.pending = pending
        self.graph = graph
        self.records: dict[str, ResultRecord] = {}
        self.refused: list[SubPlanRefused] = []
        self.unrun: list[str] = []
        self.refutation: _Refutation | None = None
        self.failure: BaseException | None = None


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


def _launch_ready(
    run: _Progress,
    in_flight: dict[asyncio.Task[_Landed], Entry],
    *,
    wiring: _Wiring,
    depth: int,
) -> BaseException | None:
    """Start every pending entry whose deps have landed; report a budget refusal instead of raising.

    Returned rather than raised so the caller can still wait out the nodes already in flight:
    raising here would abandon real dispatches with no record of them.
    """
    for entry in _ready(run.pending, run.records):
        try:
            wiring.spent.spend(limit=wiring.limits.max_nodes_per_run)
        except RunNodeBudgetExceededError as exc:
            return exc
        del run.pending[entry.spec.node_id]
        # A SNAPSHOT of the graph: a task started now must see what had landed when it
        # started, not whatever lands while it runs.
        coro = _run_entry(entry, wiring=wiring, depth=depth, admitted=dict(run.graph))
        in_flight[asyncio.ensure_future(coro)] = entry
    return None


def _ready(pending: Mapping[str, Entry], records: Mapping[str, ResultRecord]) -> list[Entry]:
    """Return the pending entries whose deps have all landed, in plan order.

    A dep naming something that is NOT an entry of this plan is already satisfied: the
    validator only admits an outside dep that names an already-admitted node
    (:func:`~agentdag.application.kernel.plan_validate.validate_plan`'s dep rule), and an
    admitted node is terminal before this plan started. Waiting for one would deadlock the
    plan on a record this loop is never going to produce.
    """
    return [e for e in pending.values() if all(dep not in pending or dep in records for dep in e.spec.deps)]


async def _await_next(in_flight: dict[asyncio.Task[_Landed], Entry]) -> list[_Landed | BaseException]:
    """Wait for the NEXT dispatch to land and return just what settled.

    ``FIRST_COMPLETED``, which is design 3.3's ``await next_terminal()``: a batch wait makes
    an entry whose own dep has landed queue behind the slowest of its peers.
    """
    settled, _ = await asyncio.wait(set(in_flight), return_when=asyncio.FIRST_COMPLETED)
    return [_result_of(task, in_flight) for task in settled]


async def _await_all(in_flight: dict[asyncio.Task[_Landed], Entry]) -> list[_Landed | BaseException]:
    """Wait out every remaining dispatch - the barrier this loop can build without a stop notice.

    Never cancels. A node in flight is a REAL dispatch with a real cost, and cancelling it to
    report something another branch decided would abandon work the journal has no record of
    (the rule :meth:`~agentdag.application.kernel.context.Coordinator.map` states for its own
    branches). Task 34's stop notice is what makes stopping one legitimate.
    """
    if not in_flight:
        return []
    settled, _ = await asyncio.wait(set(in_flight), return_when=asyncio.ALL_COMPLETED)
    return [_result_of(task, in_flight) for task in settled]


def _result_of(task: asyncio.Task[_Landed], in_flight: dict[asyncio.Task[_Landed], Entry]) -> _Landed | BaseException:
    """Take one settled task's result off the in-flight set, exception included.

    Returned rather than re-raised for the same reason as the budget refusal: whatever else
    is running still has to be waited out.
    """
    del in_flight[task]
    exc = task.exception()
    return exc if exc is not None else task.result()


async def _settle(settled: Sequence[_Landed | BaseException], run: _Progress, *, plan: Plan, wiring: _Wiring) -> None:
    """Absorb what landed, then run the two condition checks over it.

    ``async`` only so the caller reads as one sequence of awaits; it does no I/O.
    """
    landed: list[_Landed] = []
    for item in settled:
        if isinstance(item, BaseException):
            run.failure = run.failure or item
            continue
        landed.append(item)
        _absorb(item, run)
    if run.refutation is None:
        run.refutation = _first_refutation(landed, plan=plan, view=_view(wiring.ctx, run.records))


def _absorb(item: _Landed, run: _Progress) -> None:
    """Merge one landed entry - its own record, any subtree, and anything it reported."""
    run.records[item.entry.spec.node_id] = item.record
    run.records.update(item.subtree)
    run.graph[item.entry.spec.node_id] = item.entry.spec
    run.refused.extend(item.refused)
    run.unrun.extend(item.unrun)


async def _run_entry(entry: Entry, *, wiring: _Wiring, depth: int, admitted: Mapping[str, NodeSpec]) -> _Landed:
    """Dispatch one entry, recursing when it names ``plan``."""
    if entry.op == PLAN_OP:
        return await _run_sub_plan(entry, wiring=wiring, depth=depth, admitted=admitted)
    record = await _dispatch_leaf(entry, wiring=wiring)
    return _Landed(entry=entry, record=record, subtree={})


async def _dispatch_leaf(entry: Entry, *, wiring: _Wiring) -> ResultRecord:
    """Build this entry's op body and await it inside one slot of the run-wide bound.

    The slot is held around the body ALONE. A recursion must never hold one (see
    :func:`_run_sub_plan`), and neither may anything else that waits on a nested dispatch.
    """
    body = wiring.registry.get(entry.op).build(entry, wiring.ctx)
    async with wiring.ctx.co.parallel_bound():
        result = await body()
    return _record_of(result, entry=entry, ctx=wiring.ctx)


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


async def _run_sub_plan(entry: Entry, *, wiring: _Wiring, depth: int, admitted: Mapping[str, NodeSpec]) -> _Landed:
    """Dispatch this entry's planner, then execute what it planned one level deeper.

    The run-wide slot is taken for the PLANNER DISPATCH ONLY and released before the
    recursion: holding it across the sub-plan would deadlock at ``parallel=1``, because the
    sub-plan's own leaves queue for the same semaphore the parent is sitting on.

    A sub-plan the validator refused comes back as
    :class:`~agentdag.application.kernel.planner.NotPlanned`, and its reasons are carried out
    on :attr:`Executed.refused` rather than lost here. The entry's dependents never become
    ready, so they land in :attr:`Executed.unrun`.
    """
    # NOT charged here: :func:`_launch_ready` already spent for this entry, and a planner is
    # the one node a `plan` entry dispatches itself. Charging in both places billed every plan
    # entry twice, which a budget test still passed - it raised, just not for its own reason.
    async with wiring.ctx.co.parallel_bound():
        planned = await dispatch_planner(
            spec=entry.spec,
            goal=_sub_goal(entry),
            evidence=_evidence(entry, wiring.ctx),
            ctx=wiring.ctx,
            registry=wiring.registry,
            limits=wiring.limits,
            graph=admitted,
            is_root=False,
            allocate_id=wiring.ids.allocate,
        )
    if not isinstance(planned, Planned):
        refusal = SubPlanRefused(node_id=entry.spec.node_id, reasons=planned.reasons)
        return _Landed(entry=entry, record=planned.record, subtree={}, refused=(refusal,))
    sub = await _execute(planned.plan, wiring=wiring, depth=depth + 1, admitted=dict(admitted))
    return _Landed(entry=entry, record=planned.record, subtree=sub.records, refused=sub.refused, unrun=sub.unrun)


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
    """Return the first condition these records refuted, in landing order, or ``None``.

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


def _executed(plan: Plan, *, run: _Progress, view: Mapping[str, ResultRecord]) -> Executed:
    """Settle ``done_when`` over what ran and assemble the subtree's result.

    A subtree that was REFUTED, or that carries a refused sub-plan, is never done - whatever
    ``done_when`` says. Design section 3.3 evaluates ``done_when`` only after the loop runs to
    completion; a refutation leaves it through ``replan``, so the two verdicts never meet
    there. They can meet in a function that RETURNS, and the stop wins: a plan that stopped
    early has entries that never ran, so a ``done_when`` settling True over the records that
    DID land is answering a question about a plan nobody finished.
    """
    stopped = run.refutation is not None or bool(run.refused)
    unrun = tuple(run.unrun) + tuple(e.spec.node_id for e in run.pending.values())
    return Executed(
        records=run.records,
        done=not stopped and evaluate(plan.done_when, view) is True,
        fired=None if run.refutation is None else run.refutation.condition,
        fired_on=None if run.refutation is None else run.refutation.node_id,
        refused=tuple(run.refused),
        unrun=unrun,
    )
