"""The one workflow that decomposes a GOAL: plan it, run what was planned, ask when stuck.

Graph A is a fixed graph an author wrote. This one has no graph of its own: it hands the
goal to a planner node and runs whatever that plans, which is the shape the project exists
for. Reachable as ``agentdag run start plan-goal --arg goal="..."`` - a goal is a workflow
ARGUMENT, so no CLI verb of its own is needed.

The program does the two things a workflow program owes
:func:`~agentdag.application.kernel.root.run_root` and nothing else: it MINTS the two node
specs (the kernel names no tier, deadline, id or budget) and it hands over the run-scoped
allocators. Everything else it reads off the coordinator it is handed.

Contents:
    * :class:`PlanGoalArgs` - the goal, and where its work runs.
    * :func:`program` - mint the specs, then run the root ladder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from ...domain.models import Budget, Isolation, Kind, NodeSpec, TierRole
from ..kernel.execute import NodeBudget, NodeIds
from ..kernel.registry import PlanContext
from ..kernel.root import run_root

if TYPE_CHECKING:
    from ..kernel.context import Coordinator

__all__ = ["PLANNER_ID", "PlanGoalArgs", "program"]

PLANNER_ID = "p_root"
"""The root planner's node id, and the prefix of every journal line about a planning attempt."""

APPROVE_ID = "a_planning"
"""The node a person answers when the planner has used every attempt it was allowed."""

WORKTREE = "root"
"""The directory under ``wt/`` the planned entries run in.

One directory for the whole plan rather than one per entry: an entry that wants its own
isolation declares it on its own spec, which is where the design puts that decision."""

_PLANNER_DEADLINE_S = 900.0
"""How long ONE planning attempt may take. Planning is one model turn's worth of work."""

_DECIDE_BY_S = 86_400.0
"""How long the exhaustion question stands before its default applies unattended.

A day, because the default is ABANDON: nobody is asked to drop what they are doing, and a
run nobody answers stops rather than spending more."""

_PLANNER_TOKENS = 400_000
"""What one planning attempt may spend on the top row."""


class PlanGoalArgs(BaseModel):
    """What this workflow is told: the goal, and nothing else it can get from the coordinator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str = Field(min_length=1)
    """What to plan for, in the operator's own words. It becomes the root planner's brief."""


async def program(co: Coordinator, args: PlanGoalArgs) -> None:
    """Plan ``args.goal`` and run what was planned.

    Args:
        co: The coordinator; every effect goes through one of its primitives, and the op
            registry and run limits the kernel needs are read off it rather than built
            here - a program is handed the coordinator and nothing else.
        args: This run's arguments.

    Raises:
        Suspended: the planner used every attempt and nobody has answered yet - control
            flow, not an error. The coordinator process exits and a later launch with a
            decision recorded resumes exactly here.
    """
    cwd = co.run_dir.worktree(WORKTREE)
    cwd.mkdir(parents=True, exist_ok=True)
    await run_root(
        goal=args.goal,
        planner=_planner_spec(),
        approve=_approve_spec(),
        ctx=PlanContext(co=co, cwd=cwd),
        registry=co.registry,
        limits=co.policy.run_limits,
        graph={},
        spent=NodeBudget(),
        ids=NodeIds(),
    )


def _planner_spec() -> NodeSpec:
    """The root planner: the one node this workflow names itself, on the top row."""
    return NodeSpec(
        node_id=PLANNER_ID,
        kind=Kind.PLANNER,
        tier_role=TierRole.TOP,
        isolation=Isolation.NONE,
        deadline_s=_PLANNER_DEADLINE_S,
        budget=Budget(tokens={"sonnet": _PLANNER_TOKENS}),
    )


def _approve_spec() -> NodeSpec:
    """The human gate; its deadline is the decision window, not a node runtime."""
    return NodeSpec(
        node_id=APPROVE_ID,
        kind=Kind.APPROVE,
        executor="code",
        isolation=Isolation.NONE,
        deps=[PLANNER_ID],
        deadline_s=_DECIDE_BY_S,
    )
