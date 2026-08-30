"""Task 35: a refuted condition stops the subtree, waits it out, and re-plans.

Every arm drives :func:`~agentdag.application.kernel.execute.execute_plan` against a REAL
coordinator over a real run directory, with a fake only at the executor and gate ports -
the same shape ``test_kernel_execute.py`` uses, because what is under test is the loop's own
sequencing and nothing here should be able to pass by patching it.

The re-plan is driven by a planner node that WRITES a plan, exactly as production's does:
:func:`~agentdag.application.kernel.planner.dispatch_planner` reads ``plan.json`` back out
of the node directory, so a fake that returned a ``Plan`` object directly would skip the
parse and the validator - the two things most likely to reject what a real planner writes.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from kernel_fakes import FakeScanner, RedGate, fresh_run_dir, outcome, wire

from agentdag.application.kernel import subtree as subtree_module
from agentdag.application.kernel.execute import Cause, NodeBudget, NodeIds, ReplanLimitExceededError, execute_plan
from agentdag.application.kernel.registry import PlanContext
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.condition import AllOf, AnyOf, Compare, FieldRef
from agentdag.domain.models import Budget, Kind, NodeSpec, TierRole
from agentdag.domain.plan import Entry, Plan
from agentdag.domain.policy import RunLimits

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from agentdag.application.kernel.execute import Executed
    from agentdag.application.kernel.ports import ExecutorRequest
    from agentdag.domain.condition import Condition
    from agentdag.domain.models import NodeOutcome

REG = build_op_registry()

ALWAYS_TRUE = AllOf(all=())
"""Kleene AND over no children: settles True whatever the records hold."""

ALWAYS_FALSE = AnyOf(any=())
"""Kleene OR over no children: settles False whatever the records hold."""

_OP_KIND: dict[str, Kind] = {"work": Kind.WORK, "gate:make-test": Kind.GATE, "plan": Kind.PLANNER}


def limits(*, max_replans: int = 3, max_nodes_per_run: int = 1000) -> RunLimits:
    """Run limits generous everywhere but the one bound an arm names."""
    return RunLimits(
        tokens_per_row={"sonnet": 1_000_000_000},
        deadline_ceiling_s=999_999.0,
        per_kind_ceiling={},
        planner_kinds=[],
        top_role_budget_floor=0.0,
        max_replans=max_replans,
        max_nodes_per_run=max_nodes_per_run,
        max_nodes_per_plan=1000,
        max_plan_depth=5,
    )


def spec_of(node_id: str, *, op: str = "work", deps: Sequence[str] = (), deadline_s: float = 60) -> NodeSpec:
    """The node spec an entry of ``op`` dispatches under."""
    return NodeSpec(
        node_id=node_id,
        kind=_OP_KIND.get(op, Kind.WORK),
        tier_role=TierRole.STANDARD,
        deadline_s=deadline_s,
        deps=list(deps),
        budget=Budget(tokens={"sonnet": 400_000}),
    )


def entry(
    *,
    node_id: str,
    op: str = "work",
    deps: Sequence[str] = (),
    acceptance: Condition | None = None,
    args: Mapping[str, object] | None = None,
    deadline_s: float = 60,
) -> Entry:
    """Build one plan entry naming ``op``."""
    return Entry(
        spec=spec_of(node_id, op=op, deps=deps, deadline_s=deadline_s),
        op=op,
        args=dict(args or {}),
        brief=f"do {node_id}",
        output_contract=frozenset({"status", "rc"}) if op.startswith("gate") else frozenset({"status"}),
        acceptance=acceptance,
    )


def plan_with(
    entries: Sequence[Entry], *, done_when: Condition = ALWAYS_TRUE, holds_while: Condition | None = None
) -> Plan:
    """Build a plan over ``entries``."""
    return Plan(goal="g", entries=tuple(entries), done_when=done_when, holds_while=holds_while)


def rc_is_zero(node_id: str = "g") -> Compare:
    """The acceptance a RED gate refutes: that node's ``rc`` is 0."""
    return Compare(ref=FieldRef(entry=node_id, field="rc"), op="==", value=0)


def work_only_plan(goal: str, node_id: str) -> str:
    """One plan carrying a single ``work`` entry, as a planner node writes it."""
    return json.dumps(
        {
            "goal": goal,
            "entries": [
                {
                    "spec": {"node_id": node_id, "kind": "work", "deadline_s": 60.0},
                    "op": "work",
                    "args": {},
                    "brief": f"do {node_id}",
                    "output_contract": ["status"],
                    "acceptance": None,
                }
            ],
            "done_when": {"ref": {"entry": node_id, "field": "status"}, "op": "==", "value": "done"},
        }
    )


class ReplanningExecutor:
    """Records every dispatch, and writes a fresh plan whenever a PLANNER node runs.

    Keyed on the node's kind rather than on a dispatch count or an id: the re-planned
    entries get coordinator-ALLOCATED ids this fixture cannot know, and keying on a count
    would silently mean something different the moment an arm adds an entry.
    """

    def __init__(self, *, plans: Sequence[str] | None = None) -> None:
        """Start with nothing dispatched; ``plans`` is what each planner dispatch writes."""
        self.dispatched: list[str] = []
        self.planner_dispatches = 0
        self.briefs: list[str] = []
        self._plans = list(plans or [])

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Record the dispatch, and write this planner's plan if it is one."""
        node_id = request.node_dir.parent.name  # nodes/<node_id>/<hash8>
        self.dispatched.append(node_id)
        if _is_planner(request):
            self.briefs.append(request.brief)
            nth = self.planner_dispatches
            self.planner_dispatches += 1
            raw = self._plans[nth] if nth < len(self._plans) else self._plans[-1]
            (request.node_dir / "plan.json").write_text(raw, encoding="utf-8")
        return outcome({"sonnet": 10})


def _is_planner(request: ExecutorRequest) -> bool:
    """Whether this dispatch is a planner node, read off the prompt the planner op sends."""
    return "You are a planner" in request.prompt


def run_plan(
    tmp_path: Path,
    plan: Plan,
    *,
    executor: ReplanningExecutor,
    planner: NodeSpec | None,
    run_limits: RunLimits | None = None,
) -> Executed:
    """Execute ``plan`` against a real coordinator, re-planning through ``planner``."""
    run_dir = fresh_run_dir(tmp_path)
    coordinator = wire(run_dir, executor, FakeScanner(), gate_port=RedGate())  # type: ignore[arg-type]
    ctx = PlanContext(co=coordinator, cwd=run_dir.worktree("a"))
    return asyncio.run(
        execute_plan(
            plan,
            ctx=ctx,
            registry=REG,
            limits=run_limits or limits(),
            depth=0,
            spent=NodeBudget(),
            ids=NodeIds(),
            planner=planner,
        )
    )


@pytest.mark.os_agnostic
def test_the_new_plan_replaces_unexecuted_entries_and_keeps_completed_records(tmp_path: Path) -> None:
    """Task 35 step 1. Design section 4 step 4: the new plan replaces the UNEXECUTED entries.

    ``done_one`` already landed, so re-running it would spend a node to reproduce a record
    already on disk. ``never_ran`` depends on the refuting gate, so it was still pending when
    the subtree stopped and the new plan replaces it - it must never be dispatched at all.
    """
    executor = ReplanningExecutor(plans=[work_only_plan("fix it", "repair")])
    plan = plan_with(
        [
            entry(node_id="done_one"),
            entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero()),
            entry(node_id="never_ran", deps=["g"]),
        ]
    )

    out = run_plan(tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"))

    assert "done_one" in out.records
    assert executor.dispatched.count("done_one") == 1  # kept, never re-dispatched
    assert "never_ran" not in executor.dispatched  # replaced before it ever ran
    assert executor.planner_dispatches == 1


@pytest.mark.os_agnostic
def test_the_cause_carries_the_condition_the_node_and_the_values(tmp_path: Path) -> None:
    """Task 35 step 2. A re-plan saying only "something failed" makes the planner guess.

    Asserted on the VALUES, not merely that a cause object exists: the planner has to be told
    WHAT the condition read and what it found, or the next plan is written blind.

    Driven through the no-planner path, because that is where the cause is READABLE. A
    successful re-plan replaces the refuting plan, so the subtree that comes back is the NEW
    one and reports no cause - which is correct, and is why the re-planning arms below assert
    the cause through the planner's brief instead.
    """
    executor = ReplanningExecutor(plans=[work_only_plan("fix it", "repair")])
    plan = plan_with([entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero())])

    out = run_plan(tmp_path, plan, executor=executor, planner=None)

    cause = out.cause
    assert isinstance(cause, Cause)
    assert cause.node_id == "g"
    assert cause.values == {"g.rc": 1}
    assert executor.planner_dispatches == 0  # `planner=None` states there is nothing to re-dispatch


@pytest.mark.os_agnostic
def test_a_successful_re_plan_leaves_no_cause_on_the_subtree(tmp_path: Path) -> None:
    """The control for the arm above, and the thing that makes ``cause`` mean something.

    Without it, a ``cause`` that was simply never cleared would read the same as a subtree
    that genuinely still refutes, and every re-planned subtree would look broken to a parent
    branching on it.
    """
    executor = ReplanningExecutor(plans=[work_only_plan("fix it", "repair")])
    plan = plan_with([entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero())])

    out = run_plan(tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"))

    assert executor.planner_dispatches == 1
    assert out.cause is None


@pytest.mark.os_agnostic
def test_the_planner_is_briefed_with_the_cause_and_the_records_it_has(tmp_path: Path) -> None:
    """Design section 4 step 3 lists four things the planner is given; a cause is one.

    The previous plan's goal and the records that DID land are what stop the planner from
    re-emitting the entries that just failed, so they must reach its brief rather than only
    the cause. Asserted on the brief text because that is the only channel a planner node
    actually reads.
    """
    executor = ReplanningExecutor(plans=[work_only_plan("fix it", "repair")])
    plan = plan_with([entry(node_id="done_one"), entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero())])

    run_plan(tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"))

    brief = executor.briefs[0]
    assert "g.rc" in brief  # the values the condition read
    assert "done_one" in brief  # the record that landed and must not be re-planned


def always_refuting_plan(goal: str, node_id: str) -> str:
    """A plan whose single gate entry can never satisfy its own acceptance.

    The gate port is RED for every call, so ``rc == 0`` refutes on every pass - which is what
    makes the re-plan allowance, rather than the fixture, decide when the loop stops.
    """
    return json.dumps(
        {
            "goal": goal,
            "entries": [
                {
                    "spec": {"node_id": node_id, "kind": "gate", "deadline_s": 60.0},
                    "op": "gate:make-test",
                    "args": {},
                    "brief": "run the gate",
                    "output_contract": ["status", "rc"],
                    "acceptance": {"ref": {"entry": node_id, "field": "rc"}, "op": "==", "value": 0},
                }
            ],
            "done_when": {"ref": {"entry": node_id, "field": "rc"}, "op": "==", "value": 0},
        }
    )


@pytest.mark.os_agnostic
def test_replans_are_bounded_per_plan(tmp_path: Path) -> None:
    """Task 35 step 3. A plan that always refutes must stop at the allowance, not run forever.

    ``max_replans=2`` buys exactly two re-dispatches of the planner. Asserted as a COUNT
    rather than "it eventually stopped", because a bound that stops one pass late looks
    identical from the outside and costs a dispatch every time.
    """
    executor = ReplanningExecutor(plans=[always_refuting_plan("still broken", "g2")])
    plan = plan_with([entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero())])

    with pytest.raises(ReplanLimitExceededError):
        run_plan(
            tmp_path,
            plan,
            executor=executor,
            planner=spec_of("replanner", op="plan"),
            run_limits=limits(max_replans=2),
        )

    assert executor.planner_dispatches == 2


@pytest.mark.os_agnostic
def test_a_plan_that_stops_refuting_never_reaches_the_bound(tmp_path: Path) -> None:
    """The control. Without it an implementation that always exhausts passes the arm above.

    The first re-plan produces a plan with nothing to refute, so the loop must stop there
    with one planner dispatch and no exception at all.
    """
    executor = ReplanningExecutor(plans=[work_only_plan("fix it", "repair")])
    plan = plan_with([entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero())])

    out = run_plan(
        tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"), run_limits=limits(max_replans=2)
    )

    assert executor.planner_dispatches == 1
    assert out.cause is None


@pytest.mark.os_agnostic
def test_exhaustion_is_reported_to_the_parent_not_raised_through_it(tmp_path: Path) -> None:
    """Task 35 step 3. A raise that escaped would take the whole run down over one subtree.

    The parent's own ``execute_plan`` call must RETURN, carrying the exhausted sub-plan on
    ``refused`` with the reason verbatim, and must not be done - a subtree nobody could plan
    is not a subtree that finished.
    """
    executor = ReplanningExecutor(plans=[always_refuting_plan("still broken", "g2")])
    parent = plan_with([entry(node_id="p", op="plan", args={"goal": "inner"})])

    out = run_plan(tmp_path, parent, executor=executor, planner=None, run_limits=limits(max_replans=1))

    assert [r.node_id for r in out.refused] == ["p"]
    assert "max_replans" in out.refused[0].reasons[0]
    assert out.done is False


class SlowThenGateExecutor(ReplanningExecutor):
    """Like :class:`ReplanningExecutor`, but one named node takes its time to land.

    The delay is what puts a node IN FLIGHT at the moment the gate refutes, which is the only
    state in which the stop-and-wait can be observed at all: with every dispatch finishing
    instantly there is nothing for the barrier to wait for and the arm would pass vacuously.
    """

    def __init__(self, *, slow: str, seconds: float, plans: Sequence[str] | None = None) -> None:
        """Record enter/exit per dispatch, delaying ``slow`` by ``seconds``."""
        super().__init__(plans=plans)
        self.events: list[str] = []
        self._slow = slow
        self._seconds = seconds

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Log entry and exit around the dispatch, sleeping for the slow node."""
        node_id = request.node_dir.parent.name
        self.events.append(f"enter {node_id}")
        if node_id == self._slow:
            await asyncio.sleep(self._seconds)
        result = await super().run(request)
        self.events.append(f"exit {node_id}")
        return result


@pytest.mark.os_agnostic
def test_in_flight_nodes_are_waited_out_before_the_planner_is_re_dispatched(tmp_path: Path) -> None:
    """Task 35 step 4. Never re-plan around a running node.

    The gate refutes while ``slow`` is still working, and the re-dispatch must come after it
    lands: the next plan's entries would otherwise start against a worktree ``slow`` is still
    writing to.

    What this arm does NOT pin, measured by mutation: the BARRIER. Deleting the stop-and-wait
    outright leaves it green, because the unbounded drain that has always followed the pass
    already orders these two. The barrier's own contribution is the BOUND - detecting that a
    node is still running when it should not be, so the subtree is not re-planned at all -
    and that is what the timeout arm below pins. Kept because the ordering is still a real
    requirement and nothing else asserts it, not because it tests this task's new code.
    """
    executor = SlowThenGateExecutor(slow="slow", seconds=0.05, plans=[work_only_plan("fix it", "repair")])
    plan = plan_with([entry(node_id="slow"), entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero())])

    run_plan(tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"))

    assert "exit slow" in executor.events
    planner_enter = next(i for i, e in enumerate(executor.events) if e.startswith("enter replanner"))
    assert executor.events.index("exit slow") < planner_enter


@pytest.mark.os_agnostic
def test_a_barrier_timeout_fails_the_subtree_rather_than_re_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 35 step 4, and its STOP condition. A stuck node means DO NOT re-plan.

    ``slow`` outlives its own deadline, so the barrier's derived bound runs out with it still
    in flight - which means deadline enforcement itself failed, and re-planning on top of that
    is the exact race the barrier exists to prevent. The subtree is reported instead: no
    planner dispatch, not done, and the stuck node named.

    ``BARRIER_SLACK_S`` is patched down because it is 30 seconds of deliberate slack on a
    bound that is already correct - the arm is about what happens when the bound RUNS OUT,
    not about how much room the real one leaves.
    """
    monkeypatch.setattr(subtree_module, "BARRIER_SLACK_S", 0.01)
    executor = SlowThenGateExecutor(slow="slow", seconds=0.3, plans=[work_only_plan("fix it", "repair")])
    plan = plan_with(
        [
            entry(node_id="slow", deadline_s=0.01),
            entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero()),
        ]
    )

    out = run_plan(tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"))

    assert out.stuck == frozenset({"slow"})
    assert executor.planner_dispatches == 0  # the original only; no re-plan over a running node
    assert out.done is False
    assert out.cause is not None  # and it still reports WHY it stopped


@pytest.mark.os_agnostic
def test_a_node_that_lands_inside_the_bound_leaves_nothing_stuck(tmp_path: Path) -> None:
    """The control for the arm above, with the same patched slack.

    Without it an implementation that reported every in-flight node as stuck would pass, and
    no subtree would ever be re-planned again.
    """
    monkeypatch_slack = 5.0
    executor = SlowThenGateExecutor(slow="slow", seconds=0.01, plans=[work_only_plan("fix it", "repair")])
    plan = plan_with(
        [
            entry(node_id="slow", deadline_s=monkeypatch_slack),
            entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero()),
        ]
    )

    out = run_plan(tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"))

    assert out.stuck == frozenset()
    assert executor.planner_dispatches == 1
