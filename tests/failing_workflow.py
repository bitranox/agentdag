"""A workflow whose program raises, so a test can reach the coordinator's FAILED exit.

Graph A cannot get there: a node that raises becomes a FAILED RECORD, never a failed
run (``dispatch.py``'s "a raising branch is a FAILED RECORD, never a dead fleet"), so
the one exit in :func:`~agentdag.application.kernel.run._drive` that writes
``status=failed`` needs a program that raises out of the program itself.

Its own module, not a closure inside a test, because
:func:`~agentdag.application.kernel.workflow_check.assert_deterministic` reads the
MODULE's source before the first dispatch: a program defined inside a test module
would put that whole test file under the determinism check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from agentdag.application.kernel.context import Coordinator

__all__ = ["FailingArgs", "WorkflowFailedError", "program"]


class FailingArgs(BaseModel):
    """This workflow takes nothing; it exists to reach one exit."""


class WorkflowFailedError(RuntimeError):
    """What :func:`program` raises, distinctive enough to assert on."""


async def program(co: Coordinator, args: FailingArgs) -> None:
    """Raise before dispatching anything.

    Args:
        co: The coordinator; unused, the raise comes first.
        args: This workflow's (empty) arguments.

    Raises:
        WorkflowFailedError: always - that is the whole point of this workflow.
    """
    del co, args
    raise WorkflowFailedError("this workflow always fails")
