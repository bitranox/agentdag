"""RED/GREEN tests for OpRegistry: a plan entry names an op; the registry says whether one exists."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, ConfigDict

from agentdag.application.kernel.registry import Body, OpRegistry, OpSpec, PlanContext, UnregisteredOp
from agentdag.domain.kernel_errors import KernelError

if TYPE_CHECKING:
    from agentdag.domain.models import Decision, ResultRecord
    from agentdag.domain.plan import Entry


class _NoArgs(BaseModel):
    """An op with nothing beyond the entry's own spec and brief."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _build(entry: Entry, ctx: PlanContext) -> Body:
    """A build no test here ever calls - present only so ``WORK`` type-checks as a real ``OpSpec``."""
    del entry, ctx

    async def body() -> ResultRecord | Decision:
        raise NotImplementedError

    return body


WORK = OpSpec(
    name="work",
    args_model=_NoArgs,
    output_contract=frozenset({"status"}),
    can_change_state=True,
    build=_build,
)


def test_registry_refuses_duplicate_and_absent() -> None:
    reg = OpRegistry()
    reg.register(WORK)
    with pytest.raises(KernelError):
        reg.register(WORK)
    with pytest.raises(UnregisteredOp):
        reg.get("apply")  # never registered, by decision
    assert "apply" not in reg.names()


def test_registry_names_lists_exactly_the_registered_ops() -> None:
    reg = OpRegistry()
    reg.register(WORK)
    assert reg.names() == frozenset({"work"})


def test_unregistered_op_is_a_kernel_error() -> None:
    """``UnregisteredOp`` is a member of the ``KernelError`` family, not a bespoke exception."""
    reg = OpRegistry()
    with pytest.raises(KernelError):
        reg.get("apply")
