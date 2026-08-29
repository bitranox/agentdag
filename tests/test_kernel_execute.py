"""Task 33: the recursive execute loop, its two condition checks, and the two run bounds.

Every arm drives :func:`~agentdag.application.kernel.execute.execute_plan` against a REAL
:class:`~agentdag.application.kernel.context.Coordinator` over a real run directory, with a
fake at the one genuinely external edge - the executor, and the gate port. The seams under
test (deps ordering, concurrency, the condition checks, the recursion, the two bounds) are
all inside the loop itself, so nothing here patches the loop or the evaluator.

Two vacuously-decided conditions do the work of a literal, which the condition language has
no term for: ``AllOf(all=())`` is Kleene AND over no children and settles True, and
``AnyOf(any=())`` is Kleene OR over none and settles False (``condition.py``'s ``_combine``,
whose docstring names ``all([])``/``any([])`` as the same case). They are asserted below
rather than assumed, because every other arm's ``done_when`` rests on them.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from kernel_fakes import FakeScanner, RecordingExecutor, RedGate, fresh_run_dir, outcome, wire

from agentdag.application.kernel.execute import (
    Executed,
    NodeBudget,
    PlanDepthExceededError,
    RunNodeBudgetExceededError,
    execute_plan,
)
from agentdag.application.kernel.registry import PlanContext
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.condition import AllOf, AnyOf, Compare, FieldRef, evaluate
from agentdag.domain.models import Budget, Kind, NodeSpec, NodeStatus, TierRole
from agentdag.domain.plan import Entry, Plan
from agentdag.domain.policy import RunLimits

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from agentdag.application.kernel.ports import ExecutorRequest
    from agentdag.domain.condition import Condition
    from agentdag.domain.models import NodeOutcome

REG = build_op_registry()

ALWAYS_TRUE = AllOf(all=())
"""Kleene AND over no children: settles True whatever the records hold."""

ALWAYS_FALSE = AnyOf(any=())
"""Kleene OR over no children: settles False whatever the records hold."""

_OP_KIND: dict[str, Kind] = {
    "work": Kind.WORK,
    "gate:make-test": Kind.GATE,
    "plan": Kind.PLANNER,
}


def limits(*, max_nodes_per_run: int = 1000, max_nodes_per_plan: int = 1000, max_plan_depth: int = 5) -> RunLimits:
    """Build run limits with generous defaults, so an arm bounds exactly the one it names."""
    return RunLimits(
        tokens_per_row={"sonnet": 1_000_000_000},
        deadline_ceiling_s=999_999.0,
        per_kind_ceiling={},
        planner_kinds=[],
        top_role_budget_floor=0.0,
        max_replans=3,
        max_nodes_per_run=max_nodes_per_run,
        max_nodes_per_plan=max_nodes_per_plan,
        max_plan_depth=max_plan_depth,
    )


def entry(
    *,
    node_id: str,
    op: str = "work",
    deps: Sequence[str] = (),
    acceptance: Condition | None = None,
    args: Mapping[str, object] | None = None,
) -> Entry:
    """Build one plan entry naming ``op``, with the spec the coordinator dispatches."""
    spec = NodeSpec(
        node_id=node_id,
        kind=_OP_KIND.get(op, Kind.WORK),
        tier_role=TierRole.STANDARD,
        deadline_s=60,
        deps=list(deps),
        budget=Budget(tokens={"sonnet": 400_000}),
    )
    return Entry(
        spec=spec,
        op=op,
        args=dict(args or {}),
        brief=f"do {node_id}",
        output_contract=frozenset({"status"}),
        acceptance=acceptance,
    )


def plan_with(
    entries: Sequence[Entry], *, done_when: Condition = ALWAYS_TRUE, holds_while: Condition | None = None
) -> Plan:
    """Build a plan over ``entries``, defaulting ``done_when`` to the vacuous truth."""
    return Plan(goal="g", entries=tuple(entries), done_when=done_when, holds_while=holds_while)


class TraceExecutor:
    """Records an enter/exit event per dispatch, so ORDER and OVERLAP are both readable.

    ``enter``/``exit`` rather than a single event per node: a loop that dispatches two
    independent entries SERIALLY and one that dispatches them CONCURRENTLY produce the same
    call order, and only the interleaving tells them apart. The ``sleep(0)`` yields to the
    event loop between the two, which is what lets a concurrent loop interleave at all.
    """

    def __init__(self) -> None:
        """Start with an empty trace."""
        self.events: list[str] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Trace this dispatch's entry and exit around a yield to the event loop."""
        node_id = request.node_dir.parent.name
        self.events.append(f"enter {node_id}")
        await asyncio.sleep(0)
        self.events.append(f"exit {node_id}")
        return outcome({"sonnet": 10})

    @property
    def entered(self) -> list[str]:
        """The node ids in the order they were dispatched."""
        return [e.removeprefix("enter ") for e in self.events if e.startswith("enter ")]


class NestingPlannerExecutor:
    """Writes a plan.json whose only entry is another ``plan`` entry, at every depth.

    An UNBOUNDED source of nesting, deliberately: what stops the recursion is then the limit
    under test and nothing else, so an arm that raises proves the bound rather than the
    fixture running out of levels.
    """

    def __init__(self) -> None:
        """Start with no dispatches recorded."""
        self.dispatches = 0

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Write the self-nesting plan into this node's dir and report a done outcome."""
        self.dispatches += 1
        (request.node_dir / "plan.json").write_text(_NESTING_PLAN, encoding="utf-8")
        return outcome({"sonnet": 10})


def _nesting_plan() -> str:
    """One plan whose single entry asks for another sub-plan, forever."""
    return json.dumps(
        {
            "goal": "nest",
            "entries": [
                {
                    "spec": {"node_id": "sub", "kind": "planner", "deadline_s": 60.0},
                    "op": "plan",
                    "args": {"goal": "deeper"},
                    "brief": "plan one level deeper",
                    "output_contract": ["status"],
                    "acceptance": None,
                }
            ],
            "done_when": {"ref": {"entry": "sub", "field": "status"}, "op": "==", "value": "done"},
        }
    )


_NESTING_PLAN = _nesting_plan()


def _one_work_entry_plan() -> str:
    """One plan carrying a single ``work`` entry, as a planner node would write it."""
    return json.dumps(
        {
            "goal": "sub",
            "entries": [
                {
                    "spec": {"node_id": "inner", "kind": "work", "deadline_s": 60.0},
                    "op": "work",
                    "args": {},
                    "brief": "do the inner work",
                    "output_contract": ["status"],
                    "acceptance": None,
                }
            ],
            "done_when": {"ref": {"entry": "inner", "field": "status"}, "op": "==", "value": "done"},
        }
    )


class OneSubPlanExecutor:
    """Writes a one-entry sub-plan for the FIRST dispatch, then behaves as a work node.

    Keyed on the dispatch count rather than on the node id, because the sub-plan's own entry
    gets a coordinator-ALLOCATED id that this fixture cannot know in advance.
    """

    def __init__(self) -> None:
        """Start with no dispatches recorded."""
        self.dispatches = 0

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Write the sub-plan on the planner dispatch; do nothing on the work one."""
        self.dispatches += 1
        if self.dispatches == 1:
            (request.node_dir / "plan.json").write_text(_ONE_WORK_ENTRY_PLAN, encoding="utf-8")
        return outcome({"sonnet": 10})


_ONE_WORK_ENTRY_PLAN = _one_work_entry_plan()


def run_plan(
    tmp_path: Path,
    plan: Plan,
    *,
    executor: object | None = None,
    gate_port: RedGate | None = None,
    run_limits: RunLimits | None = None,
    depth: int = 0,
    spent: NodeBudget | None = None,
) -> Executed:
    """Execute ``plan`` against a real coordinator over a fresh run directory."""
    run_dir = fresh_run_dir(tmp_path)
    chosen = RecordingExecutor(outcome({"sonnet": 10})) if executor is None else executor
    coordinator = wire(run_dir, chosen, FakeScanner(), gate_port=gate_port)  # type: ignore[arg-type]
    ctx = PlanContext(co=coordinator, cwd=run_dir.worktree("a"))
    return asyncio.run(
        execute_plan(
            plan,
            ctx=ctx,
            registry=REG,
            limits=run_limits or limits(),
            depth=depth,
            spent=spent or NodeBudget(),
        )
    )


@pytest.mark.os_agnostic
def test_the_vacuous_conditions_this_suite_rests_on_settle_as_claimed() -> None:
    """Pin the two fixtures every other arm's ``done_when`` is built from.

    Without this the whole suite rests on an unasserted premise: if an empty ``AllOf`` ever
    settled ``None`` instead of ``True``, arms here would report a loop defect.
    """
    assert evaluate(ALWAYS_TRUE, {}) is True
    assert evaluate(ALWAYS_FALSE, {}) is False


@pytest.mark.os_agnostic
def test_an_entry_waits_for_its_deps_and_then_runs(tmp_path: Path) -> None:
    """Step 1: a dependent entry is not dispatched until its dep's record has landed."""
    executor = TraceExecutor()
    plan = plan_with([entry(node_id="a"), entry(node_id="b", deps=["a"])])
    out = run_plan(tmp_path, plan, executor=executor)
    assert executor.entered == ["a", "b"]
    assert executor.events == ["enter a", "exit a", "enter b", "exit b"]
    assert set(out.records) == {"a", "b"}


@pytest.mark.os_agnostic
def test_independent_entries_are_dispatched_concurrently(tmp_path: Path) -> None:
    """The arm that earns the run-wide semaphore.

    A serial loop satisfies every other arm here, ``test_an_entry_waits_for_its_deps``
    included, so without this one nothing distinguishes "dispatches ready entries under
    ``parallel``" from "dispatches them one at a time" and the shared bound is decoration.
    The coordinator these tests wire has ``parallel=2``, so two entries may overlap.
    """
    executor = TraceExecutor()
    plan = plan_with([entry(node_id="a"), entry(node_id="b")])
    run_plan(tmp_path, plan, executor=executor)
    assert executor.events == ["enter a", "enter b", "exit a", "exit b"]


@pytest.mark.os_agnostic
def test_a_refuted_acceptance_stops_the_subtree_and_names_what_fired(tmp_path: Path) -> None:
    """Step 2, check one: an entry's own ``acceptance`` refuted by its own record.

    ``done_when`` here is the vacuous truth, so ``done is False`` asserts the rule that a
    REFUTED subtree is never done however its ``done_when`` settles - the two verdicts never
    meet in design 3.3's pseudocode, because a refutation leaves the loop through
    ``replan``, and they do meet in a function that RETURNS instead.
    """
    acceptance = Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0)
    plan = plan_with([entry(node_id="g", op="gate:make-test", acceptance=acceptance)])
    out = run_plan(tmp_path, plan, gate_port=RedGate())
    assert out.done is False
    assert out.fired == acceptance
    assert out.fired_on == "g"


@pytest.mark.os_agnostic
def test_a_refuted_acceptance_stops_entries_that_had_not_started(tmp_path: Path) -> None:
    """ "Stops the subtree" is a claim about what does NOT run, so assert on that.

    ``later`` depends on the gate, so it is only ever ready AFTER the refutation; a loop
    that reported ``fired`` and carried on would still dispatch it.
    """
    executor = TraceExecutor()
    acceptance = Compare(ref=FieldRef(entry="g", field="rc"), op="==", value=0)
    plan = plan_with(
        [entry(node_id="g", op="gate:make-test", acceptance=acceptance), entry(node_id="later", deps=["g"])]
    )
    out = run_plan(tmp_path, plan, executor=executor, gate_port=RedGate())
    assert out.fired_on == "g"
    assert executor.entered == []
    assert "later" not in out.records


@pytest.mark.os_agnostic
def test_a_refuted_holds_while_stops_the_subtree_even_when_the_entry_passed(tmp_path: Path) -> None:
    """Step 2, check two: the plan-wide guard, refuted by a record that itself passed.

    ``fired_on`` names the record that LANDED, not the guard's own owner: the entry did its
    job, and what failed is the premise the plan rests on.
    """
    plan = plan_with([entry(node_id="n0")], holds_while=ALWAYS_FALSE)
    out = run_plan(tmp_path, plan)
    assert out.fired == ALWAYS_FALSE
    assert out.fired_on == "n0"
    assert out.records["n0"].status is NodeStatus.DONE


@pytest.mark.os_agnostic
def test_a_node_merely_finishing_is_not_a_trigger(tmp_path: Path) -> None:
    """Design section 4's first line, as a test.

    A DONE record with no ``acceptance`` and a satisfied ``holds_while`` must leave ``fired``
    None - otherwise every completion re-plans.
    """
    out = run_plan(tmp_path, plan_with([entry(node_id="n0")]))
    assert out.fired is None
    assert out.fired_on is None
    assert out.done is True


@pytest.mark.os_agnostic
def test_an_undecided_condition_is_not_a_refutation(tmp_path: Path) -> None:
    """Three-valued, not two: an absent field reads ``None`` and must not fire.

    A loop written as ``if not evaluate(...)`` treats ``None`` as False and fires on every
    condition naming a field the record does not carry - which is most of them, mid-run.
    """
    absent = Compare(ref=FieldRef(entry="nobody", field="rc"), op="==", value=0)
    out = run_plan(tmp_path, plan_with([entry(node_id="n0", acceptance=absent)]))
    assert out.fired is None


@pytest.mark.os_agnostic
def test_done_when_unsettled_is_not_done(tmp_path: Path) -> None:
    """``done`` is True only on a settled True; an undecided ``done_when`` is NOT done."""
    unsettled = Compare(ref=FieldRef(entry="nobody", field="rc"), op="==", value=0)
    out = run_plan(tmp_path, plan_with([entry(node_id="n0")], done_when=unsettled))
    assert out.done is False


@pytest.mark.os_agnostic
def test_a_plan_entry_recurses_and_its_records_join_the_parent(tmp_path: Path) -> None:
    """Step 3: a ``plan`` entry dispatches a planner and executes what it planned."""
    executor = OneSubPlanExecutor()
    plan = plan_with([entry(node_id="p", op="plan", args={"goal": "sub"})])
    out = run_plan(tmp_path, plan, executor=executor)
    assert "p" in out.records
    inner = [node_id for node_id in out.records if node_id != "p"]
    assert len(inner) == 1
    assert out.records[inner[0]].status is NodeStatus.DONE
    assert executor.dispatches == 2


@pytest.mark.os_agnostic
def test_a_plan_entry_is_never_dispatched_through_the_registry(tmp_path: Path) -> None:
    """``plan``'s registry body is a GUARD that raises; reaching it means the loop is wrong.

    Asserted here rather than trusted: the guard is the only thing standing between "the
    loop special-cases a plan entry" and "a sub-plan is planned and silently discarded",
    and a loop that dispatched it would surface as that raise.
    """
    executor = OneSubPlanExecutor()
    plan = plan_with([entry(node_id="p", op="plan", args={"goal": "sub"})])
    out = run_plan(tmp_path, plan, executor=executor)
    assert out.records["p"].status is NodeStatus.DONE


@pytest.mark.os_agnostic
def test_the_run_node_budget_is_enforced_across_plans(tmp_path: Path) -> None:
    """Step 4: ``max_nodes_per_run`` finally binds, having been parsed since Task 30."""
    plan = plan_with([entry(node_id=f"n{i}") for i in range(3)])
    with pytest.raises(RunNodeBudgetExceededError):
        run_plan(tmp_path, plan, run_limits=limits(max_nodes_per_run=2))


@pytest.mark.os_agnostic
def test_the_budget_counts_across_a_recursion_not_per_plan(tmp_path: Path) -> None:
    """The arm that catches a per-plan counter wearing a run-level name.

    The outer plan spends one node (the planner) and the sub-plan spends one more (its work
    entry). At a run budget of 1 the sub-plan's dispatch must be refused, even though NO
    single plan here carries more than one entry - a per-plan counter would let it through.
    """
    with pytest.raises(RunNodeBudgetExceededError):
        run_plan(
            tmp_path,
            plan_with([entry(node_id="p", op="plan", args={"goal": "sub"})]),
            executor=OneSubPlanExecutor(),
            run_limits=limits(max_nodes_per_run=1),
        )


@pytest.mark.os_agnostic
def test_the_budget_is_shared_by_the_caller_not_reset_per_call(tmp_path: Path) -> None:
    """A NodeBudget passed in already part-spent keeps its count; the loop never resets it."""
    spent = NodeBudget()
    spent.spend(limit=10)
    run_plan(tmp_path, plan_with([entry(node_id="n0")]), spent=spent)
    assert spent.spent == 2


@pytest.mark.os_agnostic
def test_nesting_past_max_plan_depth_raises_with_its_own_name(tmp_path: Path) -> None:
    """Step 5b: ``max_plan_depth`` bounds the recursion, under its own error type."""
    with pytest.raises(PlanDepthExceededError):
        run_plan(
            tmp_path,
            plan_with([entry(node_id="p", op="plan", args={"goal": "deeper"})]),
            executor=NestingPlannerExecutor(),
            run_limits=limits(max_plan_depth=2),
        )


@pytest.mark.os_agnostic
def test_depth_is_bounded_before_the_node_budget_is_spent(tmp_path: Path) -> None:
    """The arm that EARNS the separate key.

    Give the run a generous node budget and a small depth, then assert WHICH error arrives:
    an implementation that bounds nesting only through ``max_nodes_per_run`` passes the arm
    above whenever the budget happens to be small, and fails here by reporting a spend
    problem for what is a nesting runaway.
    """
    executor = NestingPlannerExecutor()
    with pytest.raises(PlanDepthExceededError):
        run_plan(
            tmp_path,
            plan_with([entry(node_id="p", op="plan", args={"goal": "deeper"})]),
            executor=executor,
            run_limits=limits(max_plan_depth=2, max_nodes_per_run=1000),
        )
    assert executor.dispatches < 10


@pytest.mark.os_agnostic
def test_a_generous_depth_with_a_small_budget_reports_the_budget(tmp_path: Path) -> None:
    """The control for the arm above: the two keys bind independently, in both directions."""
    with pytest.raises(RunNodeBudgetExceededError):
        run_plan(
            tmp_path,
            plan_with([entry(node_id="p", op="plan", args={"goal": "deeper"})]),
            executor=NestingPlannerExecutor(),
            run_limits=limits(max_plan_depth=100, max_nodes_per_run=3),
        )
