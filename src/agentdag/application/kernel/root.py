"""The ROOT of one run's planning: plan, re-plan on a refusal, then ask a person.

M6 component 3's last piece, and design section 4's ladder at the one place it has nowhere
to report to. A NESTED plan the validator refuses becomes a refusal its parent branches on
(:class:`~agentdag.application.kernel.execute.SubPlanRefused`). The root has no parent, so it
takes the ladder the rest of this project already uses for "retry, then ask" - design 2.3
rule 5 - re-dispatching its planner with the validator's reasons, bounded by
``max_replans``, and SUSPENDING into ``approve`` on exhaustion rather than failing. A
suspended run stays resumable and keeps every record it earned, which is the same reason
``on_rate_limit: suspend_run`` is a suspend rather than a failure.

This module mints nothing. The workflow program passes the planner's and the approve node's
specs, their ids, deadlines, tier and budget with them, exactly as
:func:`~agentdag.application.kernel.planner.dispatch_planner` and
:func:`~agentdag.application.kernel.execute.execute_plan` already take a planner spec from
their caller. So a goal is a workflow ARGUMENT and nothing here needs a CLI of its own.

Contents:
    * :data:`ABANDON` / :data:`GRANT` - the two option ids the exhaustion approve offers.
    * :func:`run_root` - the ladder.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ...domain.kernel_errors import KernelError
from ...domain.keys import hash8
from ...domain.models import ApproveOption, ApprovePayload
from .approve import render_for_operator
from .execute import Executed, SubPlanRefused, execute_plan
from .planner import Planned, dispatch_planner
from .ports import format_stamp

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ...domain.models import NodeSpec, ResultRecord
    from ...domain.policy import RunLimits
    from .execute import NodeBudget, NodeIds
    from .planner import NotPlanned
    from .registry import OpRegistry, PlanContext

__all__ = ["ABANDON", "GRANT", "run_root"]

ABANDON = "abandon"
"""Stop asking and report what could not be planned. The DEFAULT, and its effect is ``none``.

A default is what the deadline owner applies unattended (design 2.4), so it may never be the
option that spends: nobody watching must not mean another round of model dispatches.
"""

GRANT = "replan"
"""Give the planner another ``max_replans`` worth of attempts (user, 2026-08-30).

Terminal exhaustion was the obvious shape and the wrong one: a person who reads the reasons
can often see that one more attempt is worth it, and a run that has to be started from
scratch to find out throws away every record it earned.
"""


async def run_root(
    *,
    goal: str,
    planner: NodeSpec,
    approve: NodeSpec,
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    graph: Mapping[str, NodeSpec],
    spent: NodeBudget,
    ids: NodeIds,
) -> Executed:
    """Plan ``goal`` at the root, re-planning while the validator refuses, then ask a person.

    Args:
        goal: What this run is for; the root planner's brief.
        planner: The root planner's spec, minted by the workflow program.
        approve: The spec of the node a person answers when planning is exhausted. Its
            ``deadline_s`` IS the decision window - an approve node's deadline is how long
            the question stands, not how long a process may run.
        ctx: The coordinator to dispatch through, and the directory entries run in.
        registry: The ops a plan may name.
        limits: The run's ceilings. ``max_replans`` bounds ONE planning round here, exactly
            as it bounds one plan's re-planning inside
            :func:`~agentdag.application.kernel.execute.execute_plan`.
        graph: Nodes the workflow already dispatched, which the root plan's deps and
            conditions may name. Empty when the root plan is the whole run.
        spent: The RUN's node budget. Required, with no default, for the reason
            ``execute_plan`` requires it: an allocator default-constructed per call is one
            per call, and two of them hand out the same values.
        ids: The RUN's node-id allocator, required for the same reason.

    Returns:
        The root subtree's records and verdict, or - when a person abandoned the run - a
        refusal naming the planner and the validator's reasons.

    Raises:
        Suspended: planning is exhausted and no decision is recorded for the question yet.
            Control flow, not an error: ``_drive`` writes the suspend, notifies, and exits,
            and a later launch that has folded a decision resumes exactly here.
        KernelError: the journal has no ``run_started`` line, so the decision deadline has
            no stable base; or the planner produced no record to point the decider at.
    """
    granted = 0
    attempts = 0
    reasons: tuple[str, ...] = ()
    asked: set[str] = set()
    while True:
        planned = await _plan(goal=_ask(goal, reasons=reasons, granted=granted), planner=planner, ctx=ctx,
                              registry=registry, limits=limits, graph=graph, ids=ids)  # fmt: skip
        if isinstance(planned, Planned):
            return await execute_plan(planned.plan, ctx=ctx, registry=registry, limits=limits, depth=0,
                                      spent=spent, ids=ids, planner=planner, admitted=graph)  # fmt: skip
        reasons = planned.reasons
        if attempts < limits.max_replans:
            attempts += 1
            continue
        if not await _granted(approve, planner=planner, reasons=reasons, ctx=ctx, asked=asked):
            return _abandoned(planner, reasons=reasons, ctx=ctx)
        granted += 1
        attempts = 0


async def _plan(
    *,
    goal: str,
    planner: NodeSpec,
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    graph: Mapping[str, NodeSpec],
    ids: NodeIds,
) -> Planned | NotPlanned:
    """Dispatch the root planner once, holding the run-wide slot for the dispatch only.

    ``is_root=True`` is what turns on the two rules that are the root's alone: a ``done_when``
    that can never settle True, and one a gate's exit code alone could settle.
    """
    async with ctx.co.parallel_bound():
        return await dispatch_planner(
            spec=planner,
            goal=goal,
            evidence=dict(ctx.co.dispatcher.records),
            ctx=ctx,
            registry=registry,
            limits=limits,
            graph=graph,
            is_root=True,
            allocate_id=ids.allocate,
        )


def _ask(goal: str, *, reasons: tuple[str, ...], granted: int) -> str:
    """Compose what the planner is asked for: the goal, why the last try was refused, and
    whether a person granted this round.

    The granted round is named because a re-dispatch briefed identically to one already in
    the journal is SERVED FROM IT, not run: the key is the brief's, so a grant that changed
    no word of it would replay the dispatch the decider had just read and call that another
    round. Saying which round it is makes the dispatch real - and, because the payload points
    at the dispatch that failed, it is also what stops two exhaustions from asking the same
    question twice. It reaches the PLANNER, never the operator: design 4's payload carries no
    round counter.
    """
    if not reasons:
        return goal
    listed = "; ".join(reasons)
    asked = (
        f"{goal}\n\n"
        f"The previous plan for this goal was REFUSED before anything ran, so nothing has "
        f"been done yet. Why it was refused: {listed}. Produce a plan that fixes every "
        f"reason listed."
    )
    if not granted:
        return asked
    return f"{asked}\n\nA person read the refusals and granted planning round {granted + 1}."


async def _granted(
    approve: NodeSpec, *, planner: NodeSpec, reasons: tuple[str, ...], ctx: PlanContext, asked: set[str]
) -> bool:
    """Ask whether to abandon or to plan again, and return whether another round was granted.

    ``asked`` holds the payload hashes this LAUNCH has already had answered, and a repeat is
    refused. Unreachable while the payload keeps pointing at the failing dispatch and
    :func:`_ask` keeps the dispatches distinct, which is exactly why it is checked: a
    decision is FINAL per (node id, payload hash), so an exhaustion that rebuilt an
    identical payload would have the recorded grant RE-SERVED rather than asked again, and
    the ladder would re-plan unattended forever - spending nothing, notifying nobody, and
    never stopping. A silent infinite loop is the one outcome worse than a failed run, so the
    invariant the whole shape rests on is checked rather than trusted.

    The hash checked is the one :meth:`~agentdag.application.kernel.context.Coordinator.approve`
    itself recorded the decision under, read off the returned
    :class:`~agentdag.domain.models.Decision`, so this cannot drift from the hashing that
    decides whether a decision matches.

    Raises:
        Suspended: through :meth:`~agentdag.application.kernel.context.Coordinator.approve`,
            when this exact question has no answer recorded yet. Nothing is added to
            ``asked`` on that path: a suspend ends the launch, and the resume re-asks it.
        KernelError: this launch already had an answer served for this exact payload, so
            asking again cannot terminate.
    """
    decision = await ctx.co.approve(approve, payload=_exhausted(approve, planner=planner, reasons=reasons, ctx=ctx))
    if decision.payload_hash in asked:
        raise KernelError(
            f"the exhaustion payload hashed to {decision.payload_hash!r} a second time in one "
            "launch, so a decision already served would be served again and the planning "
            "ladder could never terminate"
        )
    asked.add(decision.payload_hash)
    return decision.decision == GRANT


def _exhausted(approve: NodeSpec, *, planner: NodeSpec, reasons: tuple[str, ...], ctx: PlanContext) -> ApprovePayload:
    """Build the question a person answers when the planner has run out of attempts.

    ``artefact_refs`` names the node directory of the planner dispatch that FAILED, and it
    does two jobs at once. The decider gets a pointer to what the planner actually wrote,
    rather than a summary of it. And it is the field that makes a repeated ask a NEW
    question: a decision is final per (node id, payload hash), so an identical payload would
    re-serve the recorded grant instead of asking again and the run would re-plan unattended
    forever. That path is the ``hash8`` of the dispatch's own journal key, so it differs
    every round - see :func:`_ask` for what keeps the dispatches themselves distinct.
    """
    return ApprovePayload(
        text=_question(planner, reasons=reasons),
        node_id=approve.node_id,
        artefact_refs=[_failed_dispatch_ref(planner, ctx=ctx)],
        options=[
            ApproveOption(id=ABANDON, label="abandon: report what could not be planned", effect="none"),
            ApproveOption(id=GRANT, label="grant the planner another round of attempts", effect="external"),
        ],
        default=ABANDON,
        decide_by=_decide_by(ctx, window_s=approve.deadline_s),
        workflow=ctx.co.workflow,
        run_id=ctx.co.run_id,
    )


def _question(planner: NodeSpec, *, reasons: tuple[str, ...]) -> str:
    """Render the reasons for a person, through the renderer that makes model-quoted text askable."""
    listed = "\n".join(f"  - {reason}" for reason in reasons)
    return render_for_operator(
        f"The root planner {planner.node_id!r} has used every attempt it was allowed and "
        f"still has no plan this run will accept.\n"
        f"The last one was refused for:\n{listed}\n\n"
        f"Abandon the run, or grant another round of planning attempts."
    )


def _failed_dispatch_ref(planner: NodeSpec, *, ctx: PlanContext) -> str:
    """Return the run-relative node directory of the planner dispatch that just failed.

    Raises:
        KernelError: the planner has no record, so there is no dispatch to point at. It
            cannot happen on this path - the caller has just read reasons off one - and it is
            checked rather than asserted away because a payload with an empty
            ``artefact_refs`` would be the one shape that CAN re-serve an old decision.
    """
    record = _record_of(planner, ctx=ctx)
    if record is None:
        raise KernelError(f"planner {planner.node_id!r} produced no record, so the decider has nothing to read")
    node_dir = ctx.co.run_dir.node_dir(planner.node_id, hash8(record.input_hash))
    return node_dir.relative_to(ctx.co.run_dir.root).as_posix()


def _decide_by(ctx: PlanContext, *, window_s: float) -> str:
    """Return the decision deadline: the run's OWN start plus the approve node's window.

    Read from ``run_started`` rather than from the clock for the reason graph A reads it the
    same way: the payload's content hash IS this node's dispatch identity, so a deadline
    taken from now would move on every launch and ask a new question each time.

    Raises:
        KernelError: the journal has no ``run_started`` line, which
            :func:`~agentdag.application.kernel.run.run_coordinator` always writes before the
            program runs.
    """
    started = ctx.co.dispatcher.index.run_started
    if started is None:
        raise KernelError("this run's journal has no run_started line, so the approve deadline has no stable base")
    return format_stamp(datetime.fromisoformat(started.at) + timedelta(seconds=window_s))


def _abandoned(planner: NodeSpec, *, reasons: tuple[str, ...], ctx: PlanContext) -> Executed:
    """Report a root nobody could plan and a person chose not to keep planning.

    Not done and not raised: the run finishes normally, and what it produced is a refusal
    carrying the validator's reasons verbatim - the same shape a parent already reads for a
    sub-plan the validator would not take.
    """
    record = _record_of(planner, ctx=ctx)
    return Executed(
        records={} if record is None else {planner.node_id: record},
        done=False,
        cause=None,
        refused=(SubPlanRefused(node_id=planner.node_id, reasons=reasons),),
        unrun=(),
    )


def _record_of(planner: NodeSpec, *, ctx: PlanContext) -> ResultRecord | None:
    """Return the planner's CURRENT record - the dispatch that produced the last plan."""
    return ctx.co.dispatcher.records.get(planner.node_id)
