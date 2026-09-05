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
    * :func:`working_directory` - the run's own worktree, or the workspace it was given.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...domain.kernel_errors import KernelError
from ...domain.models import Budget, Isolation, Kind, NodeSpec, TierRole
from ..kernel.execute import NodeBudget, NodeIds
from ..kernel.registry import PlanContext
from ..kernel.root import run_root, with_budget_grants

if TYPE_CHECKING:
    from ..kernel.context import Coordinator

__all__ = ["PLANNER_ID", "PlanGoalArgs", "program", "working_directory"]

PLANNER_ID = "p_root"
"""The root planner's node id, and the prefix of every journal line about a planning attempt."""

APPROVE_ID = "a_planning"
"""The node a person answers when the planner has used every attempt it was allowed."""

BUDGET_APPROVE_ID = "a_budget"
"""The node a person answers when the RUN has spent its node budget.

Its OWN node rather than a second question on :data:`APPROVE_ID`, and that is forced rather
than stylistic: a decision is recorded per (node id, payload hash), and
:func:`~agentdag.application.kernel.root.with_budget_grants` has to count the budget grants
at run start from the journal alone. Sharing a node would leave it unable to tell which
decisions were about the budget without rebuilding every payload the run ever offered."""

WORKTREE = "root"
"""The directory under ``wt/`` the planned entries run in when no workspace is named.

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
    """What this workflow is told: the goal, where to work on it, and nothing else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str = Field(min_length=1)
    """What to plan for, in the operator's own words. It becomes the root planner's brief."""

    workspace: Path | None = None
    """The directory this plan works IN, or ``None`` to work in a worktree the run owns.

    A workspace is a SECOND isolation root: the run directory still holds every node's
    bookkeeping and is still the only thing the isolation scan watches, while the tree the
    plan actually changes lives outside it, where the operator can see it and keep it after
    the run is gone. Every dispatch is bounded by both roots and by nothing else
    (:attr:`~agentdag.application.kernel.ports.ExecutorRequest.extra_roots`).

    Always ABSOLUTE and fully resolved once validated - see :meth:`_resolved_workspace` for
    why that has to happen here rather than at the first use."""

    @field_validator("workspace", mode="before")
    @classmethod
    def _resolved_workspace(cls, value: object) -> object:
        """Expand and resolve the workspace at the boundary, refusing a blank one.

        Resolved HERE because ``Path("./ws").parents`` is ``[Path(".")]``: a containment
        check against an unresolved relative path inspects the process's own directory and
        passes, so every guard downstream would be inspecting the wrong thing. Doing it at
        validation also fixes the value that reaches ``state.json``, and a relaunch - a
        background child, a resume, an approve - re-validates an ALREADY absolute path
        rather than re-resolving a relative one against whatever cwd that process has.

        Args:
            value: The raw argument: ``None``, or a path as typed on the command line.

        Returns:
            The resolved absolute path, or ``None``; anything else is handed back for
            pydantic to reject with its own type error.

        Raises:
            ValueError: the argument is present but blank. ``--arg workspace=`` is a typo,
                and it is not the same statement as omitting the argument - ``Path("")``
                resolves to the process's own working directory, which would silently make
                that the root a plan writes in.
        """
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            raise ValueError("workspace must not be blank; omit it to work in a worktree the run owns")
        if not isinstance(value, (str, Path)):
            return value
        return Path(value).expanduser().resolve()


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
    cwd = working_directory(co, args.workspace)
    await run_root(
        goal=args.goal,
        planner=_planner_spec(),
        approve=_approve_spec(),
        budget_approve=_budget_approve_spec(),
        ctx=PlanContext(co=co, cwd=cwd, workspace=args.workspace),
        registry=co.registry,
        limits=with_budget_grants(co.policy.run_limits, co=co, approve_id=BUDGET_APPROVE_ID),
        graph={},
        spent=NodeBudget(),
        ids=NodeIds(),
    )


def working_directory(co: Coordinator, workspace: Path | None) -> Path:
    """Return where this plan works, refusing a workspace nothing could work in.

    With no workspace, a worktree the run owns, created here because nothing else does. With
    one, the operator's own directory, which is NEVER created: a mistyped path would then be
    made somewhere on disk and the plan would work in an empty tree believing it was the
    project. The two refusals below are about USABILITY - an operator finds out before the
    first node spends anything. What makes a workspace SAFE is decided by the coordinator,
    which refuses one inside the run root and bounds every dispatch by both roots.

    Args:
        co: The coordinator, for the run directory the default worktree lives under.
        workspace: :attr:`PlanGoalArgs.workspace`, already resolved.

    Returns:
        The directory every planned entry runs in.

    Raises:
        KernelError: the named workspace does not exist, or is not a directory.
    """
    if workspace is None:
        cwd = co.run_dir.worktree(WORKTREE)
        cwd.mkdir(parents=True, exist_ok=True)
        return cwd
    if not workspace.exists():
        raise KernelError(
            f"workspace {workspace} does not exist; create it, or omit --arg workspace to work in the run"
        )
    if not workspace.is_dir():
        raise KernelError(f"workspace {workspace} is not a directory")
    return workspace


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


def _budget_approve_spec() -> NodeSpec:
    """The human gate for a run that has spent its node budget.

    No ``deps``: unlike the planning gate this question is about the whole RUN rather than
    about what one planner produced, and it can be reached before that planner's own
    exhaustion ever is.
    """
    return NodeSpec(
        node_id=BUDGET_APPROVE_ID,
        kind=Kind.APPROVE,
        executor="code",
        isolation=Isolation.NONE,
        deadline_s=_DECIDE_BY_S,
    )
