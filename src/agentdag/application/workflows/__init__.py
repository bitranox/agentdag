"""The built-in workflow programs, keyed by the name a run names on the command line.

A workflow is three things the coordinator needs and one it checks: a name, the model
its arguments parse into, the program to run, and the MODULE the program lives in -
which :func:`~agentdag.application.kernel.workflow_check.assert_deterministic` reads the
source of before the first dispatch, so a program that reaches for the clock or
randomness is refused rather than silently re-dispatching work on the next launch.

Contents:
    * :class:`WorkflowDef` - one workflow: its name, args model, program and module.
    * :data:`WORKFLOWS` - every built-in workflow, by name.
    * :func:`get_workflow` - look one up, or refuse with a typed error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...domain.errors import WorkflowNotFound
from . import graph_a

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import ModuleType

    from pydantic import BaseModel

    from ..kernel.context import Coordinator

__all__ = ["WORKFLOWS", "WorkflowDef", "get_workflow"]


@dataclass(frozen=True, slots=True)
class WorkflowDef:
    """One built-in workflow, as the coordinator runner needs it.

    Attributes:
        name: The name a run selects this workflow by.
        args_model: The pydantic model the run's arguments parse into; also what a
            resume validates a stored ``state.args`` back through.
        program: The graph as code, awaited once per launch with the coordinator and
            the parsed arguments.
        module: The module ``program`` is defined in, so its source can be checked for
            nondeterministic calls before anything is dispatched.
    """

    name: str
    args_model: type[BaseModel]
    program: Callable[[Coordinator, Any], Awaitable[None]]
    module: ModuleType


WORKFLOWS: dict[str, WorkflowDef] = {
    "graph-a": WorkflowDef(
        name="graph-a",
        args_model=graph_a.GraphAArgs,
        program=graph_a.program,
        module=graph_a,
    ),
}
"""Every built-in workflow, by name."""


def get_workflow(name: str) -> WorkflowDef:
    """Return the built-in workflow called ``name``.

    Args:
        name: The workflow name.

    Returns:
        Its definition.

    Raises:
        WorkflowNotFound: no built-in workflow has that name; the message lists the
            names there are, so the caller does not have to guess.

    Example:
        >>> get_workflow("graph-a").name
        'graph-a'
        >>> get_workflow("nope")
        Traceback (most recent call last):
        agentdag.domain.errors.WorkflowNotFound: no workflow named 'nope'; known: ['graph-a']
    """
    try:
        return WORKFLOWS[name]
    except KeyError as exc:
        raise WorkflowNotFound(f"no workflow named {name!r}; known: {sorted(WORKFLOWS)}") from exc
