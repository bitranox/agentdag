"""The recursive execute loop: run one plan's entries to terminal, recursing on sub-plans.

M6 component 3, and design section 3.3's scheduler in code. Entries are dispatched as soon as
their ``deps`` are satisfied, bounded by the run-wide semaphore, and CONTINUOUSLY: the loop
waits for the next record to land - never for a whole batch - so an entry whose own dep has
landed starts at once rather than behind the slowest of its peers. Each landed record goes
through the design's TWO condition checks: the entry's own ``acceptance``, and the plan-wide
``holds_while``. A ``plan`` entry is SPECIAL-CASED into a recursion rather than dispatched
through the registry, which is why the registry's ``plan`` body is a guard that raises
(:func:`~agentdag.composition.kernel.build_op_registry`).

A refuted condition does not end the subtree. The stop notice goes out, the barrier waits the
nodes already in flight out, and the plan's ``planner`` is re-dispatched with the cause (design
section 4), bounded by ``max_replans``; what comes back on :class:`Executed` is what the LAST
plan produced. Three events of that loop reach the journal - a plan accepted, a plan the
validator refused, and a subtree's own done verdict - each carrying the journal key of the
planner dispatch it belongs to, so a reader can join it to that node's own ``started`` and
``result`` lines.

What this module deliberately does NOT do, and where it is owed:

* **It never cancels.** A node still running when its subtree stops is ASKED to hand over, never
  killed (design constraint 2), so "stops the subtree" means "notify what is running, start
  nothing further, and wait it out". A node still in flight when the barrier's bound runs out
  leaves the subtree :attr:`Executed.stuck` and un-re-planned rather than interrupted.
* **It does not read ``spec.isolation``.** Where an entry runs is component 8's subject (user,
  2026-08-30); ``Isolation`` remains parsed-never-enforced. Every entry runs in ``ctx.cwd``.

Contents:
    * :class:`RunNodeBudgetExceededError` / :class:`PlanDepthExceededError` - the run bounds' errors.
    * :class:`ReplanLimitExceededError` - a plan that spent its re-plan allowance and still refutes.
    * :class:`NodeBudget` - how many nodes this RUN has dispatched, shared across every plan.
    * :class:`NodeIds` - the RUN's node-id allocator, shared for the same reason.
    * :class:`SubPlanRefused` - one sub-plan the validator refused, with its reasons verbatim.
    * :class:`Cause` - what the re-dispatched planner is told fired, with the values it read.
    * :class:`Executed` - one subtree's records, whether it is done, and why it stopped.
    * :func:`execute_plan` - the loop itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Protocol

from ...domain.condition import evaluate, referenceable_view, referenced_fields
from ...domain.journal import SubtreeDoneLine
from ...domain.kernel_errors import KernelError
from ...domain.models import NodeStatus, ResultRecord
from ...domain.plan import evaluate_holds_while
from .planner import Planned, dispatch_planner
from .ports import stamp
from .subtree import StopScope, barrier, deadline_bound

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...domain.condition import Condition
    from ...domain.models import NodeSpec
    from ...domain.plan import Entry, Plan
    from ...domain.policy import RunLimits
    from .registry import OpRegistry, PlanContext

__all__ = [
    "Cause",
    "Executed",
    "GrantMoreReplans",
    "NodeBudget",
    "NodeIds",
    "PlanDepthExceededError",
    "ReplanLimitExceededError",
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


class ReplanLimitExceededError(KernelError):
    """A plan spent ``max_replans`` and its condition still refutes.

    Raised INSIDE a subtree and never allowed to escape :func:`execute_plan`: the boundary
    turns it into an :class:`Executed` whose entry record is FAILED, which is what the PARENT
    plan branches on (design section 4 step 3). An exception rather than a return value only
    because the recursion would otherwise have to thread an exhaustion flag back through
    every level, and a flag threaded through five signatures is a flag someone drops.
    """


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
class Cause:
    """What the re-dispatched planner is told fired, with VALUES - never prose.

    A re-plan briefed with "something failed" makes the planner guess at what to fix, so the
    values the condition actually read travel with it. They are read off the same
    :func:`~agentdag.domain.condition.referenceable_view` the evaluator used, so the planner
    is told what the CHECK saw rather than a second rendering of the record that might not
    agree with it.
    """

    condition: Condition
    """The condition that settled False - an entry's ``acceptance`` or the plan's
    ``holds_while``."""

    node_id: str
    """The node whose landed record refuted it. For a ``holds_while`` that is the record that
    LANDED, not the guard's owner: the entry may itself have passed."""

    values: Mapping[str, object]
    """Every field the condition referenced, as ``"<entry>.<field>": value``.

    A field whose record has not landed is ABSENT rather than None: None is a value a record
    can genuinely hold, so an absent key and a null one must not read the same to the planner.
    """


@dataclass(frozen=True, slots=True)
class Executed:
    """What running one plan's subtree produced, and why it stopped."""

    records: Mapping[str, ResultRecord]
    """Every terminal record of this subtree, by node id - a nested sub-plan's own records
    joined in, so a parent reads one flat mapping however deep the recursion went."""

    done: bool
    """Whether ``plan.done_when`` settled TRUE and nothing refuted. An UNDECIDED ``done_when``
    is not done: a completion condition that cannot be settled has not been met."""

    cause: Cause | None
    """Why this subtree stopped, if a condition refuted it: what fired, on which node, and
    the values it read. ``None`` when nothing refuted - which includes every undecided
    condition, because three-valued means an absent field is not a failure.

    ONE object rather than a condition beside a node id beside a value map: the three are one
    fact, and the planner is briefed with all three or with none of them."""

    refused: tuple[SubPlanRefused, ...]
    """Every sub-plan of this subtree the validator refused, at any depth, in the order they
    landed. Empty when none was. A DIFFERENT cause from :attr:`fired` and kept apart from it
    on purpose: a refuted premise and a planner that emitted an invalid plan need different
    briefs when the planner is re-dispatched."""

    unrun: tuple[str, ...]
    """The entries of this subtree that were never dispatched, in plan order - because the
    subtree stopped, or because their deps never landed. Design section 4 step 4 is what
    needs this: "the new plan replaces S's UNEXECUTED entries", and this names them."""

    stuck: frozenset[str] = frozenset()
    """The nodes still in flight when the barrier's bound ran out, empty in the ordinary case.

    Non-empty means deadline enforcement itself failed, and it is why this subtree was NOT
    re-planned: re-planning around a node still writing to the worktree is the exact race the
    barrier exists to prevent (Task 34)."""


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


class GrantMoreReplans(Protocol):
    """Asked whether a plan that spent its re-plan allowance may have another.

    The seam by which the ROOT's "retry, then ask a person" ladder reaches a loop that must
    know nothing about approvals: :func:`execute_plan` calls this and branches on a bool,
    while what it costs - an ``approve`` node, a suspend, a resumed launch - is entirely the
    caller's. Declared here rather than in ``ports.py`` because both of its arguments are
    this module's own types.

    A granted round buys another ``max_replans``, which is what GRANT already means one
    ladder up (:data:`~agentdag.application.kernel.root.GRANT`). One word, one meaning.
    """

    async def __call__(self, *, plan: Plan, cause: Cause, granted: int) -> bool:
        """Return whether to keep re-planning ``plan``, which ``cause`` has just stopped again.

        Args:
            plan: The plan that was running - the LAST one, not the one the run opened with,
                so a caller rendering it for a person shows what actually stopped.
            cause: The condition that refuted, on which node, and the values it read.
            granted: How many rounds this plan has already been granted, 0 the first time.

        Returns:
            True to spend another ``max_replans`` on it; False to abandon.
        """
        ...


async def execute_plan(
    plan: Plan,
    *,
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    depth: int,
    spent: NodeBudget,
    ids: NodeIds,
    planner: NodeSpec | None,
    admitted: Mapping[str, NodeSpec] | None = None,
    replans: int = 0,
    grant_more: GrantMoreReplans | None = None,
) -> Executed:
    """Run one plan's entries to terminal, re-planning a refuted subtree, recursing on ``op="plan"``.

    Design section 4's loop in code. A condition that settles False stops the subtree - the
    notice goes out, the barrier waits the in-flight nodes out, and NOTHING is cancelled -
    and then ``planner`` is re-dispatched with the cause. The new plan replaces the entries
    that never ran; the ones that completed keep their records and are never re-dispatched,
    because re-running a completed entry spends a node to reproduce a record already on disk.

    Args:
        plan: The validated plan to run. Already through
            :func:`~agentdag.application.kernel.plan_validate.validate_plan`, so its ops are
            registered, its ids allocated and its conditions within contract.
        ctx: The coordinator to dispatch through, and the directory entries run in.
        registry: The ops an entry may name.
        limits: The run's ceilings; ``max_nodes_per_run`` and ``max_plan_depth`` bind here.
        depth: How deep this plan sits, 0 for the run's own top-level plan.
        spent: The RUN's node budget, shared with every other plan of this run.
        ids: The RUN's node-id allocator, shared for the same reason. REQUIRED, with no
            default, and that is the guard: a second top-level call on the SAME run - which
            is exactly what this task's re-dispatch is - would otherwise restart at
            ``n-0001`` and collide with ids already in the journal. A default made that a
            docstring's job, and a docstring is what failed the first time this went wrong.
        planner: The planner node whose re-dispatch produces this plan's REPLACEMENT - the
            ``plan`` entry's own spec for a sub-plan, the root planner's for the root.
            REQUIRED and explicitly ``None``-able rather than defaulted, for the same reason
            ``ids`` is required: a caller that simply omitted it would get a subtree that
            silently never re-plans, which is the whole behaviour this function exists for.
            ``None`` states that this plan has no planner to re-dispatch - a hand-authored
            plan - and such a subtree reports its refutation instead, exactly as it did
            before this task.
        admitted: The nodes admitted ABOVE this plan, by node id - what a sub-plan's deps and
            conditions may name besides its own entries (``validate_plan``'s ``graph``).
            ``None`` at the root. Threaded rather than read off the coordinator because the
            dispatcher keeps RECORDS, not specs, so this loop is the only place the specs of
            already-run entries exist.
        replans: How many re-plans THIS plan has already spent, so a resumed or nested call
            cannot restart the allowance.
        grant_more: Asked when this plan has spent ``limits.max_replans`` and its condition
            STILL refutes: True buys another ``max_replans``, False abandons. Supplied only
            by the ROOT (:func:`~agentdag.application.kernel.root.run_root`), and root-only
            BY CONSTRUCTION rather than by a depth test - the recursion into a sub-plan
            simply does not pass it, so a sub-plan keeps reporting its exhaustion to the
            parent that can branch on it. ``None`` therefore means what it meant before this
            existed: exhaustion raises.

    Returns:
        This subtree's records, whether it is done, and why it stopped.

    Raises:
        PlanDepthExceededError: ``depth`` has reached ``limits.max_plan_depth``.
        RunNodeBudgetExceededError: a dispatch would cross ``limits.max_nodes_per_run``.
        ReplanLimitExceededError: this plan spent ``limits.max_replans`` and still refutes.
    """
    wiring = _Wiring(ctx=ctx, registry=registry, limits=limits, spent=spent, ids=ids)
    return await _execute(
        plan,
        wiring=wiring,
        depth=depth,
        admitted=dict(admitted or {}),
        planner=planner,
        replans=replans,
        grant_more=grant_more,
    )


async def _execute(
    plan: Plan,
    *,
    wiring: _Wiring,
    depth: int,
    admitted: dict[str, NodeSpec],
    planner: NodeSpec | None,
    replans: int = 0,
    parent: StopScope | None = None,
    grant_more: GrantMoreReplans | None = None,
) -> Executed:
    """Drive one plan to terminal, re-planning while a condition refutes and the allowance holds.

    The recursion re-enters HERE, sharing ``wiring``. One turn of this loop is one PLAN: a
    pass over its entries, and - if something refuted - a stop, a wait, and a re-dispatch of
    ``planner`` whose new plan becomes the next turn's.

    Records CARRY ACROSS turns. An entry that completed under the old plan keeps its record
    and is never re-dispatched (design section 4 step 4), so each pass starts holding
    everything the previous ones landed.
    """
    _refuse_too_deep(depth, wiring.limits)
    current = plan
    kept: dict[str, ResultRecord] = {}
    refused: list[SubPlanRefused] = []
    spent_replans = replans
    granted = 0
    while True:
        run = await _one_pass(current, wiring=wiring, depth=depth, admitted=admitted, kept=kept, parent=parent)
        if run.failure is not None:
            raise run.failure
        kept = dict(run.records)
        refused.extend(run.refused)
        run.refused = list(refused)
        view = _view(wiring.ctx, run.records)
        refutation = run.refutation
        # Three separate reasons NOT to re-plan, spelled apart because they mean different
        # things to whoever reads the returned Executed: this plan has no planner to
        # re-dispatch (a hand-authored plan), nothing refuted (the ordinary case), or the
        # barrier could not wait the subtree out - and that last one is the race the barrier
        # exists to prevent, so the subtree is reported rather than re-planned around a node
        # still writing to the worktree.
        if planner is None or refutation is None or run.stuck:
            executed = _executed(current, run=run, view=view)
            _journal_subtree_done(planner, done=executed.done, wiring=wiring)
            return executed
        cause = _cause_of(refutation, view=view)
        if spent_replans >= wiring.limits.max_replans:
            if grant_more is None:
                # No ladder above this plan, so exhaustion is a VERDICT rather than an error
                # escaping unreported: the parent turns it into a refusal it can branch on,
                # and without this line the journal would show a run of accepted plans under
                # one node id and then simply stop.
                _journal_subtree_done(planner, done=_verdict(current, run=run, view=view), wiring=wiring)
                raise ReplanLimitExceededError(
                    f"plan {current.goal!r} spent max_replans={wiring.limits.max_replans} "
                    f"and its condition still refutes on node {refutation.node_id!r}"
                )
            if not await grant_more(plan=current, cause=cause, granted=granted):
                # Abandoned. Reported, never raised: the run finishes normally holding every
                # record it earned, which is the same shape a person abandoning the OTHER
                # root ladder already gets (``root._abandoned``).
                executed = _executed(current, run=run, view=view)
                _journal_subtree_done(planner, done=executed.done, wiring=wiring)
                return executed
            granted += 1
            spent_replans = 0
        planned = await _replan(
            current, cause=cause, planner=planner, wiring=wiring, admitted=admitted, granted=granted
        )
        if planned is None:
            return _executed(current, run=run, view=view)
        spent_replans += 1
        current = planned


def _cause_of(refutation: _Refutation, *, view: Mapping[str, ResultRecord]) -> Cause:
    """Build the cause the next planner is briefed with, values included.

    The values come from the same ``referenceable_view`` the evaluator read, so the planner
    is told what the CHECK saw. A field whose record has not landed is left OUT rather than
    recorded as None, because None is a value a record can genuinely hold.

    Takes the refutation rather than the whole pass, so there is no optional to assert away:
    a caller that has not established one cannot call this at all.
    """
    values: dict[str, object] = {}
    for ref in referenced_fields(refutation.condition):
        record = view.get(ref.entry)
        if record is not None and ref.field in referenceable_view(record):
            values[f"{ref.entry}.{ref.field}"] = referenceable_view(record)[ref.field]
    return Cause(condition=refutation.condition, node_id=refutation.node_id, values=values)


def _journal_subtree_done(planner: NodeSpec | None, *, done: bool, wiring: _Wiring) -> None:
    """Record this subtree's verdict, when a planner dispatch produced the plan it ran.

    Nothing is written for a plan NOBODY PLANNED - a hand-authored plan handed straight to
    :func:`execute_plan`, or a ``planner`` spec this loop never had cause to dispatch. All
    three of these lines identify a subtree by its planner node and by the journal key that
    node was dispatched under, and such a plan has neither; the schema requires both to be
    non-empty, so the honest answer is no line rather than an id no dispatch ever produced.

    The key is read off the planner's CURRENT record rather than carried down from where the
    plan was accepted, and that is the point: a re-planned subtree ran the plan its LAST
    dispatch produced, so that is the dispatch this verdict belongs to.
    """
    if planner is None:
        return
    record = wiring.ctx.co.dispatcher.records.get(planner.node_id)
    if record is None:
        return
    wiring.ctx.co.dispatcher.journal.append(
        SubtreeDoneLine(key=record.input_hash, node_id=planner.node_id, done=done, at=stamp(wiring.ctx.co.clock))
    )


async def _replan(
    plan: Plan,
    *,
    cause: Cause,
    planner: NodeSpec,
    wiring: _Wiring,
    admitted: Mapping[str, NodeSpec],
    granted: int = 0,
) -> Plan | None:
    """Re-dispatch ``planner`` with the cause, returning the plan it produced or None.

    None when the planner wrote nothing the validator would accept. That is NOT silently the
    same as "no re-plan was wanted": the caller returns the subtree as refuted, which is the
    honest report, and the re-dispatch is not charged against the allowance a second time.
    """
    async with wiring.ctx.co.parallel_bound():
        planned = await dispatch_planner(
            spec=planner,
            goal=_replan_goal(plan, cause, granted=granted),
            evidence=dict(wiring.ctx.co.dispatcher.records),
            ctx=wiring.ctx,
            registry=wiring.registry,
            limits=wiring.limits,
            graph=admitted,
            is_root=False,
            allocate_id=wiring.ids.allocate,
        )
    return planned.plan if isinstance(planned, Planned) else None


def _replan_goal(plan: Plan, cause: Cause, *, granted: int = 0) -> str:
    """Compose what the re-dispatched planner is asked for: the goal, and what stopped the last try.

    The values are rendered rather than summarised - a planner told only that something
    failed writes the next plan blind, and the whole point of :class:`Cause` is that it
    carries what the condition READ.

    A GRANTED round is named. :func:`~agentdag.application.kernel.root._ask` names it one
    ladder up because there it is load-bearing: that ladder re-asks with the same goal and
    the same reasons, so without the round the brief is identical, the resumed launch serves
    the dispatch from its replay index, and the grant buys nothing.

    That argument does NOT carry over here, and it was measured rather than assumed: this
    brief renders ``cause.node_id``, node ids are allocated fresh for every accepted plan, so
    consecutive rounds already differ and removing this line leaves the ordinary path
    working. What it defends is the case where they do NOT differ - a refutation landing on
    an ADMITTED node, whose id is stable across rounds, with the values it read unchanged.
    Then the whole triple repeats, the re-dispatch is served, and the ladder asks again
    having done nothing. One line to make the property hold outright rather than by an
    incidental property of the id allocator.

    It reaches the PLANNER only; design 4's payload carries no counter.
    """
    read = ", ".join(f"{name}={value!r}" for name, value in sorted(cause.values.items())) or "(nothing had landed)"
    asked = (
        f"{plan.goal}\n\n"
        f"The previous plan for this goal was stopped: a condition over node {cause.node_id!r} "
        f"settled false. What the condition read: {read}. "
        f"Plan the REMAINING work. Entries that already completed keep their records and must "
        f"not be repeated."
    )
    if not granted:
        return asked
    return f"{asked}\n\nA person read the refutation and granted planning round {granted + 1}."


async def _one_pass(
    plan: Plan,
    *,
    wiring: _Wiring,
    depth: int,
    admitted: dict[str, NodeSpec],
    kept: Mapping[str, ResultRecord],
    parent: StopScope | None,
) -> _Progress:
    """Run one plan's entries until something refutes or nothing is left, then drain.

    Its own :class:`~agentdag.application.kernel.subtree.StopScope`, one per PASS: the notice
    is a property of the plan being abandoned, and a scope shared with the next plan would
    have the new plan's nodes born already stopping.
    """
    run = _Progress(pending={e.spec.node_id: e for e in plan.entries}, graph=admitted)
    run.records.update(kept)
    scope = StopScope(parent)
    # The op bodies this pass builds must ask about THIS pass's scope, and a body closes over
    # the context it was built with - so the scope travels on a context of this pass's own,
    # never by mutating the shared one, which would hand the next plan's nodes a scope that
    # is already stopping.
    passing = replace(wiring, ctx=replace(wiring.ctx, stopping=scope))
    in_flight: dict[asyncio.Task[_Landed], Entry] = {}
    while run.refutation is None and run.failure is None:
        run.failure = _launch_ready(run, in_flight, wiring=passing, depth=depth, scope=scope)
        if not in_flight or run.failure is not None:
            break
        await _settle(await _await_next(in_flight), run, plan=plan, wiring=passing)
    if run.refutation is not None and in_flight:
        run.stuck = await _stop_and_wait(scope, wiring=passing, in_flight=in_flight)
    await _settle(await _await_all(in_flight), run, plan=plan, wiring=passing)
    return run


async def _stop_and_wait(
    scope: StopScope, *, wiring: _Wiring, in_flight: Mapping[asyncio.Task[_Landed], Entry]
) -> frozenset[str]:
    """Ask the subtree to stop, then wait it out. Returns whoever was still running at the bound.

    NOTHING is cancelled here (Task 34, design constraint 2): a node in flight is a real
    dispatch whose work is evidence, so it is asked to hand over and then waited for. The
    bound is derived from the in-flight nodes' own REMAINING deadlines, read off the entries
    this pass is holding - which is why no timeout knob reaches this function.

    A ``plan`` entry is bounded by its PLANNER's deadline while its whole recursion is in
    flight, so its bound can be shorter than the subtree below it actually needs. That
    under-estimates in the SAFE direction: the barrier reports it stuck, and a stuck subtree
    fails rather than being re-planned around.

    The drain that follows this call is unbounded on purpose. The bound decides whether the
    subtree may be RE-PLANNED, never whether the loop may abandon a running task: cancelling
    is forbidden, and walking away from an un-awaited task is strictly worse than waiting.
    """
    scope.request_stop()
    graph = {entry.spec.node_id: entry.spec for entry in in_flight.values()}
    bound = deadline_bound(scope, graph, now=wiring.ctx.co.clock.now(), ceiling_s=wiring.limits.deadline_ceiling_s)
    return await barrier(scope, deadline_bound_s=bound)


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
        self.stuck: frozenset[str] = frozenset()


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
    scope: StopScope,
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
        coro = _run_entry(entry, wiring=wiring, depth=depth, admitted=dict(run.graph), scope=scope)
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


async def _run_entry(
    entry: Entry, *, wiring: _Wiring, depth: int, admitted: Mapping[str, NodeSpec], scope: StopScope
) -> _Landed:
    """Dispatch one entry, recursing when it names ``plan``, and leave the scope whatever happens.

    The entry ENTERS the scope inside the dispatch that starts its deadline, never here: the
    wait for a run-wide slot happens first and must not be charged against the node. It
    LEAVES here, so a ``plan`` entry stays in flight for its whole recursion rather than only
    for its planner dispatch - the barrier has to wait out the subtree below it, not just the
    node that planned it.

    ``finally`` rather than a happy-path call: an entry that raised is no longer running, and
    a scope that still held it would make the barrier wait out a node that is already gone.
    """
    try:
        if entry.op == PLAN_OP:
            return await _run_sub_plan(entry, wiring=wiring, depth=depth, admitted=admitted, scope=scope)
        record = await _dispatch_leaf(entry, wiring=wiring, scope=scope)
        return _Landed(entry=entry, record=record, subtree={})
    finally:
        scope.leave(entry.spec.node_id, NodeStatus.FAILED)


async def _dispatch_leaf(entry: Entry, *, wiring: _Wiring, scope: StopScope) -> ResultRecord:
    """Build this entry's op body and await it inside one slot of the run-wide bound.

    The slot is held around the body ALONE. A recursion must never hold one (see
    :func:`_run_sub_plan`), and neither may anything else that waits on a nested dispatch.

    The scope is entered AFTER the slot is acquired, stamped with the clock reading at that
    moment: the node's deadline starts when it starts running, and stamping it while it
    queued would charge the wait for a slot against the node and under-estimate the barrier's
    bound (:func:`~agentdag.application.kernel.subtree.deadline_bound`).
    """
    body = wiring.registry.get(entry.op).build(entry, wiring.ctx)
    async with wiring.ctx.co.parallel_bound():
        scope.enter(entry.spec.node_id, wiring.ctx.co.clock.now())
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


async def _run_sub_plan(
    entry: Entry, *, wiring: _Wiring, depth: int, admitted: Mapping[str, NodeSpec], scope: StopScope
) -> _Landed:
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
        scope.enter(entry.spec.node_id, wiring.ctx.co.clock.now())
        planned = await dispatch_planner(
            is_stopping=partial(scope.is_stopping, entry.spec.node_id),
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
    try:
        sub = await _execute(
            planned.plan,
            wiring=wiring,
            depth=depth + 1,
            admitted=dict(admitted),
            planner=entry.spec,
            parent=scope,
        )
    except ReplanLimitExceededError as exc:
        # Exhaustion is REPORTED to the parent, never raised through it: a raise here would
        # take the whole run down over one subtree the parent may well be able to branch
        # around. It comes out as a refusal rather than as a synthesised FAILED record,
        # because this entry has no record of its own - it borrows the PLANNER node's, and
        # rewriting that to say FAILED would make the journal misreport what that node did.
        refusal = SubPlanRefused(node_id=entry.spec.node_id, reasons=(str(exc),))
        return _Landed(entry=entry, record=planned.record, subtree={}, refused=(refusal,))
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


def _verdict(plan: Plan, *, run: _Progress, view: Mapping[str, ResultRecord]) -> bool:
    """Whether this subtree is done: its own ``done_when`` settled True, and nothing stopped it.

    One definition read by two callers - the :class:`Executed` a parent branches on, and the
    :class:`~agentdag.domain.journal.SubtreeDoneLine` a reader branches on later. Spelling it
    twice is how the journal comes to report a verdict the run never acted on.
    """
    stopped = run.refutation is not None or bool(run.refused)
    return not stopped and evaluate(plan.done_when, view) is True


def _executed(plan: Plan, *, run: _Progress, view: Mapping[str, ResultRecord]) -> Executed:
    """Settle ``done_when`` over what ran and assemble the subtree's result.

    A subtree that was REFUTED, or that carries a refused sub-plan, is never done - whatever
    ``done_when`` says. Design section 3.3 evaluates ``done_when`` only after the loop runs to
    completion; a refutation leaves it through ``replan``, so the two verdicts never meet
    there. They can meet in a function that RETURNS, and the stop wins: a plan that stopped
    early has entries that never ran, so a ``done_when`` settling True over the records that
    DID land is answering a question about a plan nobody finished.
    """
    unrun = tuple(run.unrun) + tuple(e.spec.node_id for e in run.pending.values())
    return Executed(
        records=run.records,
        done=_verdict(plan, run=run, view=view),
        cause=None if run.refutation is None else _cause_of(run.refutation, view=view),
        refused=tuple(run.refused),
        unrun=unrun,
        stuck=run.stuck,
    )
