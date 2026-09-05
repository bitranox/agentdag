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
    description="a fixture op: these arms are about registration and lookup, never about dispatch",
    args_model=_NoArgs,
    output_contract=frozenset({"status"}),
    # None, so this fixture stays exempt from the covers-the-contract invariant: these tests
    # are about registration and lookup, not about what a do-nothing run reads.
    facts_if_no_work=None,
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
}


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
    for name in sorted(reg.names()):
        unemitted = sorted(reg.get(name).output_contract - emitted)
        assert not unemitted, f"op {name!r} promises {unemitted}, which no body emits"


def test_status_is_referenceable_on_every_op_including_one_with_an_empty_contract() -> None:
    """MINOR 9: ``status`` is reserved, so even ``plan`` (which emits nothing) can be referenced."""
    reg = build_op_registry()
    assert reg.get("plan").output_contract == frozenset()
    assert "status" in RESERVED_TOP_LEVEL_FIELDS


def test_every_op_that_can_read_the_same_run_or_not_declares_that_reading() -> None:
    """MINOR 7: a scan reads; it changes nothing, so it cannot be a root plan's completion lever.

    The ops that DECLARE a no-work record are exactly those that still RUN in a run which
    accomplished nothing, and whose reading is then what it would have been anyway. That
    includes ``reduce:count``, which the old boolean put on the other side for a good reason
    (its count is 0 with nothing dispatched and N once N passed) - and that reason is exactly
    why ``count == 0`` is the do-nothing reading it must now declare.

    ``work``, ``approve`` and ``plan`` declare ``None``: running one IS the accomplishment, so
    in a do-nothing run there is no record of them at all, and their absence is what rescues a
    root plan from the gate-alone shape.
    """
    reg = build_op_registry()
    declares = {name for name in reg.names() if reg.get(name).facts_if_no_work is not None}
    assert declares == {"gate:make-test", "scan", "reduce:count"}
    assert {name for name in reg.names() if reg.get(name).facts_if_no_work is None} == {"approve", "plan", "work"}


def test_a_declared_no_work_record_must_cover_the_whole_output_contract() -> None:
    """A field left out is ABSENT from the synthesized record, so its comparison goes undecided
    rather than True - which silently widens what a root plan may settle on. The registry
    refuses the omission at registration rather than letting decision 4 quietly stop guarding
    that field, so the next contract entry cannot reopen the hole by being forgotten.
    """
    reg = OpRegistry()
    short = OpSpec(
        name="two-fields",
        description="a fixture op whose declared no-work record leaves one contract field out",
        args_model=_NoArgs,
        output_contract=frozenset({"a", "b"}),
        facts_if_no_work={"a": 0},
        build=_build,
    )
    with pytest.raises(KernelError, match="output_contract"):
        reg.register(short)

    reg.register(
        OpSpec(
            name="two-fields",
            description="the same fixture op, now declaring a value for every contract field",
            args_model=_NoArgs,
            output_contract=frozenset({"a", "b"}),
            facts_if_no_work={"a": 0, "b": False},
            build=_build,
        )
    )
    assert reg.get("two-fields").facts_if_no_work == {"a": 0, "b": False}


KERNEL_SOURCE = Path(__file__).resolve().parent.parent / "src" / "agentdag" / "composition" / "kernel.py"

_STATE_MARKER = "# state:"


def _op_name_of(call: ast.Call) -> str | None:
    """Return the ``name=`` literal of an ``OpSpec(...)`` call."""
    for keyword in call.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _comment_block_above(call: ast.Call, lines: list[str]) -> str | None:
    """Return the contiguous comment block directly above this call's ``facts_if_no_work=``.

    Walks UP from the declaration through unbroken ``#`` lines and joins them, rather than
    reading only the single line above: a one-line reason cannot say what a do-nothing run
    reads for an op with eight contract fields, and a reason worth writing should not have to
    fit the parser. The marker is looked for anywhere in the block, so ``# state:`` opens it
    and the rest continues it.
    """
    for keyword in call.keywords:
        if keyword.arg != "facts_if_no_work":
            continue
        block: list[str] = []
        cursor = keyword.lineno - 2
        while cursor >= 0 and lines[cursor].strip().startswith("#"):
            block.append(lines[cursor].strip().removeprefix("#").strip())
            cursor -= 1
        return " ".join(reversed(block)) if block else None
    return None


def flag_reasons() -> dict[str, str]:
    """Map each op the composition root registers to the reason recorded beside its declaration.

    Parsed from ``composition/kernel.py``'s own source: every ``OpSpec(...)`` must carry a
    ``# state:`` comment block directly above its ``facts_if_no_work=``. An op whose
    registration has none is absent from this mapping, which is what the test asserts
    on - so the reason is auditable at the registration rather than asserted in a report.
    """
    text = KERNEL_SOURCE.read_text(encoding="utf-8")
    lines = text.splitlines()
    reasons: dict[str, str] = {}
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "OpSpec":
            continue
        name = _op_name_of(node)
        above = _comment_block_above(node, lines)
        marker = _STATE_MARKER.removeprefix("#").strip()
        if name is None or above is None or marker not in above:
            continue
        # From the marker, not from the top of the block: two registrations carry further
        # commentary above their reason, and requiring the block to OPEN with the marker
        # reported those as having no reason at all.
        reasons[name] = above[above.index(marker) + len(marker) :].strip()
    return reasons


def test_every_registration_records_why_its_no_work_record_is_what_it_is() -> None:
    """MINOR 5: the table has to be AUDITABLE at the registration, not asserted elsewhere.

    A previous pass re-derived ``scan``'s entry alone and reported the whole table as
    re-derived. A reason written beside each registration is what makes the next reader able
    to check one op without re-deriving all six - and what makes an unexamined op visible
    as an omission rather than invisible as a default.

    The control that this parse is not vacuously empty is the equality below: it fails just as
    loudly if the scan finds NO reasons at all as if it finds six of seven.
    """
    registered = build_op_registry().names()
    reasons = flag_reasons()
    assert set(reasons) == set(registered), f"no '{_STATE_MARKER}' reason beside: {sorted(registered - set(reasons))}"
    thin = {name: reason for name, reason in reasons.items() if len(reason) < 30}
    assert not thin, f"the reason beside these flags says nothing: {thin}"


def test_judge_is_not_registered_until_component_5() -> None:
    """Checkpoint B (user, 2026-08-29): `judge` is refused by ABSENCE, like `apply`.

    Once `plan` has a real body, a registered-but-raising `judge` would be the only op that
    validates by NAME and then raises at dispatch: the plan is accepted, and the run dies
    mid-flight, after spend. Unregistering moves that refusal to plan-accept time.

    It also retires an unverified declaration rather than shipping a guess. `judge` was once
    registered `can_change_state=True` above a comment saying UNVERIFIED - no body existed, so
    no emitter had been read - and decision 4's rule is decided against exactly that
    declaration. Component 5 writes it by reading the emitter it writes.
    """
    assert "judge" not in build_op_registry().names()
