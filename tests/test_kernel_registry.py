"""RED/GREEN tests for OpRegistry: a plan entry names an op; the registry says whether one exists."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, ConfigDict

from agentdag.application.kernel.registry import Body, OpRegistry, OpSpec, PlanContext, UnregisteredOpError
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.condition import RESERVED_TOP_LEVEL_FIELDS
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
    with pytest.raises(UnregisteredOpError):
        reg.get("apply")  # never registered, by decision
    assert "apply" not in reg.names()


def test_registry_names_lists_exactly_the_registered_ops() -> None:
    reg = OpRegistry()
    reg.register(WORK)
    assert reg.names() == frozenset({"work"})


def test_unregistered_op_is_a_kernel_error() -> None:
    """``UnregisteredOpError`` is a member of the ``KernelError`` family, not a bespoke exception."""
    reg = OpRegistry()
    with pytest.raises(KernelError):
        reg.get("apply")


REGISTERED_CONTRACTS: dict[str, frozenset[str]] = {
    # Re-derived by READING each emitting body, not from the brief's illustrative comment.
    # `work` dispatches through Coordinator.work -> the wired executor, which has four
    # outcome constructors, all of them real records a condition could name:
    #   adapters/kernel/executor_claude.py:493   turns, first_turn_input_tokens
    #   adapters/kernel/executor_claude.py:1179  context_at_handover, handover_at_tokens,
    #                                            first_turn_input_tokens, grace_used, grace_expired
    #   adapters/kernel/executor_claude.py:1296  cap_hit, first_turn_input_tokens
    #   adapters/kernel/executor_claude.py:1400  deadline_hit, first_turn_input_tokens
    "work": frozenset(
        {
            "turns",
            "first_turn_input_tokens",
            "context_at_handover",
            "handover_at_tokens",
            "grace_used",
            "grace_expired",
            "cap_hit",
            "deadline_hit",
        }
    ),
    "gate:make-test": frozenset({"rc"}),  # application/kernel/context.py:499
    "scan": frozenset({"stray"}),  # application/kernel/context.py:584
    "reduce:count": frozenset({"count"}),  # composition/kernel.py:356, _build_reduce_count's fold
    "approve": frozenset({"decision"}),  # application/kernel/context.py:798
    "plan": frozenset(),  # the not-yet-wired body raises; it emits nothing
    "judge": frozenset({"verdict"}),  # NO BODY YET - the brief's binding name, unverified
}

UNVERIFIED_CONTRACT_OPS = frozenset({"judge"})
"""Ops whose contract names a field no shipped body emits, because they have no body yet."""


def emitted_key_fact_names() -> frozenset[str]:
    """Every string key any ``key_facts={...}`` literal under ``src/`` actually writes.

    Parsed from the AST rather than grepped: a grep for a bare field name matches its own
    mention in a docstring, a schema or this registry, which is exactly the fiction the
    check exists to catch.
    """
    root = Path(__file__).resolve().parent.parent / "src" / "agentdag"
    names: set[str] = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.keyword) or node.arg != "key_facts":
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            names.update(
                key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    return frozenset(names)


def test_registered_output_contracts_are_the_ones_the_table_pins() -> None:
    """CRITICAL 1: the table is re-derived from the emitting bodies, not from the brief."""
    reg = build_op_registry()
    assert {name: reg.get(name).output_contract for name in reg.names()} == REGISTERED_CONTRACTS


def test_every_contract_field_is_one_some_body_actually_emits() -> None:
    """CRITICAL 1: a contract naming a field nothing emits makes Task 31 validate against fiction.

    The control is ``artifact_ref``: the value this registry shipped for ``work`` and the
    known NEGATIVE for this check - it appears nowhere in any ``key_facts`` literal (the
    codebase spells the top-level field ``artefact_refs``), so a scan that reported it as
    emitted would be measuring the wrong thing.
    """
    emitted = emitted_key_fact_names()
    assert "artifact_ref" not in emitted  # the control: this is what the defect claimed
    assert "rc" in emitted  # a positive control: the scan does find real emissions

    reg = build_op_registry()
    for name in sorted(reg.names() - UNVERIFIED_CONTRACT_OPS):
        unemitted = sorted(reg.get(name).output_contract - emitted)
        assert not unemitted, f"op {name!r} promises {unemitted}, which no body emits"


def test_status_is_referenceable_on_every_op_including_one_with_an_empty_contract() -> None:
    """MINOR 9: ``status`` is reserved, so even ``plan`` (which emits nothing) can be referenced."""
    reg = build_op_registry()
    assert reg.get("plan").output_contract == frozenset()
    assert "status" in RESERVED_TOP_LEVEL_FIELDS


def test_only_gates_and_the_read_only_scan_cannot_change_state() -> None:
    """MINOR 7: a scan reads; it changes nothing, so it cannot be a root plan's completion lever."""
    reg = build_op_registry()
    cannot = {name for name in reg.names() if not reg.get(name).can_change_state}
    assert cannot == {"gate:make-test", "scan"}


KERNEL_SOURCE = Path(__file__).resolve().parent.parent / "src" / "agentdag" / "composition" / "kernel.py"

_STATE_MARKER = "# state:"


def _op_name_of(call: ast.Call) -> str | None:
    """Return the ``name=`` literal of an ``OpSpec(...)`` call."""
    for keyword in call.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _line_above_the_flag(call: ast.Call, lines: list[str]) -> str | None:
    """Return the source line directly above this call's ``can_change_state=``, stripped."""
    for keyword in call.keywords:
        if keyword.arg == "can_change_state":
            return lines[keyword.lineno - 2].strip()
    return None


def flag_reasons() -> dict[str, str]:
    """Map each op the composition root registers to the reason recorded beside its flag.

    Parsed from ``composition/kernel.py``'s own source: every ``OpSpec(...)`` must carry a
    ``# state:`` comment on the line directly above its ``can_change_state=``. An op whose
    registration has no such line is absent from this mapping, which is what the test asserts
    on - so the reason is auditable at the registration rather than asserted in a report.
    """
    text = KERNEL_SOURCE.read_text(encoding="utf-8")
    lines = text.splitlines()
    reasons: dict[str, str] = {}
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "OpSpec":
            continue
        name = _op_name_of(node)
        above = _line_above_the_flag(node, lines)
        if name is not None and above is not None and above.startswith(_STATE_MARKER):
            reasons[name] = above.removeprefix(_STATE_MARKER).strip()
    return reasons


def test_every_registration_records_why_its_can_change_state_flag_is_what_it_is() -> None:
    """MINOR 5: the flag table has to be AUDITABLE at the registration, not asserted elsewhere.

    A previous pass re-derived ``scan``'s flag alone and reported the whole table as
    re-derived. A reason written beside each registration is what makes the next reader able
    to check one op without re-deriving all seven - and what makes an unexamined flag visible
    as an omission rather than invisible as a default.

    The control that this parse is not vacuously empty is the equality below: it fails just as
    loudly if the scan finds NO reasons at all as if it finds six of seven.
    """
    registered = build_op_registry().names()
    reasons = flag_reasons()
    assert set(reasons) == set(registered), f"no '{_STATE_MARKER}' reason beside: {sorted(registered - set(reasons))}"
    thin = {name: reason for name, reason in reasons.items() if len(reason) < 30}
    assert not thin, f"the reason beside these flags says nothing: {thin}"
