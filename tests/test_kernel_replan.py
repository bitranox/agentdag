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
from typing import TYPE_CHECKING, TypeVar

import pytest
from kernel_fakes import FakeScanner, RedGate, fresh_run_dir, outcome, wire

from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel import subtree as subtree_module
from agentdag.application.kernel.execute import Cause, NodeBudget, NodeIds, ReplanLimitExceededError, execute_plan
from agentdag.application.kernel.registry import PlanContext
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.condition import AllOf, AnyOf, Compare, FieldRef
from agentdag.domain.journal import PlanAcceptedLine, PlanInvalidatedLine, SubtreeDoneLine
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


def work_only_plan(goal: str, node_id: str, brief: str | None = None) -> str:
    """One plan carrying a single ``work`` entry, as a planner node writes it."""
    return json.dumps(
        {
            "goal": goal,
            "entries": [
                {
                    "spec": {"node_id": node_id, "kind": "work", "deadline_s": 60.0},
                    "op": "work",
                    "args": {},
                    "brief": brief or f"do {node_id}",
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
    gate_port: RedGate | None = None,
) -> Executed:
    """Execute ``plan`` against a real coordinator, re-planning through ``planner``."""
    run_dir = fresh_run_dir(tmp_path)
    coordinator = wire(run_dir, executor, FakeScanner(), gate_port=gate_port or RedGate())  # type: ignore[arg-type]
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

    def __init__(
        self, *, slow: str, seconds: float, plans: Sequence[str] | None = None, released: asyncio.Event | None = None
    ) -> None:
        """Record enter/exit per dispatch, delaying ``slow`` by ``seconds``.

        ``released``, when given, is waited on BEFORE the delay starts, so the delay is
        measured from a point in the run rather than from the dispatch. An arm that needs the
        slow node still in flight at some later moment must not express that as "longer than
        whatever happens in between": see
        :func:`test_a_barrier_timeout_fails_the_subtree_rather_than_re_planning`.
        """
        super().__init__(plans=plans)
        self.events: list[str] = []
        self._slow = slow
        self._seconds = seconds
        self._released = released

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Log entry and exit around the dispatch, sleeping for the slow node."""
        node_id = request.node_dir.parent.name
        self.events.append(f"enter {node_id}")
        if node_id == self._slow:
            if self._released is not None:
                await self._released.wait()
            await asyncio.sleep(self._seconds)
        result = await super().run(request)
        self.events.append(f"exit {node_id}")
        return result


class ReleasingRedGate(RedGate):
    """A RED gate that releases the slow node as it answers.

    The seam that makes the barrier-timeout arm deterministic. The gate is a PORT, not an
    executor dispatch, so nothing the executor does can observe it landing; without this the
    arm can only say "sleep longer than the gate takes", which is a comparison between two
    durations and loses on a slow runner.
    """

    def __init__(self, released: asyncio.Event) -> None:
        """Answer red, and release ``released`` as the answer is given."""
        super().__init__()
        self._released = released

    def run(self, worktree: Path, log: Path) -> int:
        """Release the slow node, then report a red gate."""
        self._released.set()
        return super().run(worktree, log)


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

    ``slow`` starts its delay when the GATE answers, not when it is dispatched, and that is
    what makes the arm deterministic rather than a race. Timing it from the dispatch means
    asserting the delay outlasts the gate's own dispatch, which is a comparison between two
    durations: measured 2026-08-31, a Windows runner spent longer dispatching the gate than
    the 0.3 s the delay allowed, so ``slow`` had already landed, nothing was in flight, the
    barrier was never reached and the arm failed claiming nothing was stuck. Released from
    the gate, the only thing the delay has to outlast is in-process bookkeeping.
    """
    monkeypatch.setattr(subtree_module, "BARRIER_SLACK_S", 0.01)
    released = asyncio.Event()
    executor = SlowThenGateExecutor(
        slow="slow", seconds=0.2, plans=[work_only_plan("fix it", "repair")], released=released
    )
    plan = plan_with(
        [
            entry(node_id="slow", deadline_s=0.01),
            entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero()),
        ]
    )

    out = run_plan(
        tmp_path,
        plan,
        executor=executor,
        planner=spec_of("replanner", op="plan"),
        gate_port=ReleasingRedGate(released),
    )

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


class NoticeWatchingExecutor(ReplanningExecutor):
    """Polls its own stop predicate while it works, recording whether the notice arrived.

    A node cannot be asked whether it was notified after the fact - the predicate is read at
    the executor's turn seam and nowhere else - so the only honest observation is to read it
    from INSIDE a dispatch that is still running, which is exactly when the subtree stops.
    """

    def __init__(self, *, watch: str, plans: Sequence[str] | None = None) -> None:
        """Watch ``watch``'s dispatch for the stop notice."""
        super().__init__(plans=plans)
        self.notified: set[str] = set()
        self._watch = watch

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Poll the predicate while the watched node works, then behave as usual."""
        node_id = request.node_dir.parent.name
        if node_id == self._watch:
            for _ in range(400):  # bounded: a notice that never comes must FAIL, never hang
                if request.is_stopping is not None and request.is_stopping():
                    self.notified.add(node_id)
                    break
                await asyncio.sleep(0.001)
        return await super().run(request)


@pytest.mark.os_agnostic
def test_a_node_still_running_when_its_subtree_refutes_gets_the_stop_notice(tmp_path: Path) -> None:
    """The wire the barrier exists for: request_stop must reach a node that is still working.

    Without it the scope is a predicate nothing reads - the subtree stops on paper, every node
    runs to its natural end, and the barrier waits out full deadlines for nodes that were
    never asked to hand over.
    """
    executor = NoticeWatchingExecutor(watch="slow", plans=[work_only_plan("fix it", "repair")])
    plan = plan_with([entry(node_id="slow"), entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero())])

    run_plan(tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"))

    assert executor.notified == {"slow"}


@pytest.mark.os_agnostic
def test_a_node_whose_subtree_never_refutes_is_never_notified(tmp_path: Path) -> None:
    """The control. Without it an executor whose predicate answered True always would pass.

    The gate is absent, so nothing refutes and nothing may be asked to hand over - and the
    watched node's own poll runs to its bound and records nothing.
    """
    executor = NoticeWatchingExecutor(watch="slow", plans=[work_only_plan("fix it", "repair")])
    plan = plan_with([entry(node_id="slow"), entry(node_id="other")])

    run_plan(tmp_path, plan, executor=executor, planner=spec_of("replanner", op="plan"))

    assert executor.notified == set()
    assert executor.planner_dispatches == 0


def two_subplan_parent(*, holds_while: Condition | None) -> Plan:
    """A parent whose two entries each plan a subtree of their own."""
    return plan_with(
        [
            entry(node_id="left", op="plan", args={"goal": "left work"}),
            entry(node_id="right", op="plan", args={"goal": "right work"}),
        ],
        holds_while=holds_while,
    )


class SubtreeNoticeExecutor(ReplanningExecutor):
    """Plans a one-work-entry subtree per planner, and watches every WORK node for the notice.

    Every work node polls its own predicate, so which subtree was notified is read off the
    set rather than inferred from which nodes ran - a stopped node is NOT cancelled, so it
    finishes either way and the records alone cannot tell the two apart.
    """

    def __init__(self, *, plans: Sequence[str]) -> None:
        """Watch every work node this executor is handed."""
        super().__init__(plans=plans)
        self.notified: set[str] = set()

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Poll for the notice on a work node; write the staged plan on a planner node.

        Recorded by BRIEF, not by node id: a sub-plan's entries get ids ALLOCATED by the
        coordinator (``n-0001`` and on), so whatever the plan JSON named them is overwritten
        before they ever run, and an assertion on those names would compare against ids the
        fixture cannot know.
        """
        if not _is_planner(request):
            for _ in range(150):  # bounded, so an absent notice FAILS rather than hangs
                if request.is_stopping is not None and request.is_stopping():
                    self.notified.add(request.brief)
                    break
                await asyncio.sleep(0.001)
        return await super().run(request)


def gate_then_work_plan(goal: str, gate_id: str, work_brief: str) -> str:
    """A subtree whose gate refutes while its own work node is still running."""
    return json.dumps(
        {
            "goal": goal,
            "entries": [
                {
                    "spec": {"node_id": "w", "kind": "work", "deadline_s": 60.0},
                    "op": "work",
                    "args": {},
                    "brief": work_brief,
                    "output_contract": ["status"],
                    "acceptance": None,
                },
                {
                    "spec": {"node_id": gate_id, "kind": "gate", "deadline_s": 60.0},
                    "op": "gate:make-test",
                    "args": {},
                    "brief": "run the gate",
                    "output_contract": ["status", "rc"],
                    "acceptance": {"ref": {"entry": gate_id, "field": "rc"}, "op": "==", "value": 0},
                },
            ],
            "done_when": {"ref": {"entry": "w", "field": "status"}, "op": "==", "value": "done"},
        }
    )


@pytest.mark.os_agnostic
def test_a_sibling_subtree_is_not_notified_when_its_neighbour_re_plans(tmp_path: Path) -> None:
    """Design section 4's safe direction: a sibling is affected ONLY through the PARENT's premise.

    ``left``'s own gate refutes, so left's subtree stops. The parent declares no
    ``holds_while``, so nothing has been said about ``right`` - notifying it because its
    neighbour refuted would re-plan WRONG rather than LATE.

    Readable only because a stopped node is not cancelled: both work nodes finish either way,
    so the records cannot tell them apart and the NOTICE is the whole signal.
    """
    executor = SubtreeNoticeExecutor(
        plans=[gate_then_work_plan("left", "lg", "do lw"), work_only_plan("right", "rw", "do rw")]
    )

    run_plan(tmp_path, two_subplan_parent(holds_while=None), executor=executor, planner=None)

    assert "do lw" in executor.notified  # its own subtree refuted
    assert "do rw" not in executor.notified  # the neighbour's refutation did not reach it


@pytest.mark.os_agnostic
def test_a_parent_premise_notifies_the_subtrees_below_it(tmp_path: Path) -> None:
    """The control for the arm above, in the direction where the rule must ACT.

    Without it an implementation that never notified a NESTED node would pass, and the
    premise-at-the-parent half of the rule would be untested. The parent's ``holds_while`` is
    a premise it declared over both subtrees, so when it refutes the notice must reach past
    the plan entries into the subtrees they planned.

    The parent carries a fast gate entry purely to make the moment deterministic:
    ``holds_while`` is evaluated when a record LANDS, and a parent whose only entries are
    plan entries cannot evaluate it until a whole subtree has finished - by which time there
    may be nothing left in flight to notify, and the arm would pass or fail on timing.
    """
    executor = SubtreeNoticeExecutor(
        plans=[work_only_plan("left", "lw", "do lw"), work_only_plan("right", "rw", "do rw")]
    )
    parent = plan_with(
        [
            entry(node_id="tick", op="gate:make-test"),
            entry(node_id="left", op="plan", args={"goal": "left work"}),
            entry(node_id="right", op="plan", args={"goal": "right work"}),
        ],
        holds_while=ALWAYS_FALSE,
    )

    run_plan(tmp_path, parent, executor=executor, planner=None)

    assert executor.notified >= {"do lw", "do rw"}


@pytest.mark.os_agnostic
def test_a_refuted_entry_acceptance_reaches_a_sibling_entry_s_whole_subtree(tmp_path: Path) -> None:
    """DECIDED with the user 2026-08-30: "subtree" in design section 4 means the PLAN.

    A gate entry's own acceptance refutes while a sibling ``plan`` entry's subtree is still
    working, and the notice reaches down into it. That is deliberate and is the conservative
    direction: stopping never cancels, so a node notified unnecessarily still finishes, and
    the cost is a hand-over nobody needed rather than work lost. The alternative - only a
    plan's ``holds_while`` propagating into nested subtrees - would let a re-plan start while
    a nested subtree was still writing, which is close to the race the barrier exists for.

    The neighbouring sibling arm is NOT in tension with this: there the refutation happens
    inside a CHILD scope and so cannot climb, which is what "a sibling is affected only
    through a premise its PARENT declared" actually constrains.
    """
    executor = SubtreeNoticeExecutor(plans=[work_only_plan("under", "uw", "do uw")])
    parent = plan_with(
        [
            entry(node_id="g", op="gate:make-test", acceptance=rc_is_zero()),
            entry(node_id="below", op="plan", args={"goal": "nested work"}),
        ]
    )

    run_plan(tmp_path, parent, executor=executor, planner=None)

    assert "do uw" in executor.notified


_LINE = TypeVar("_LINE", PlanAcceptedLine, PlanInvalidatedLine, SubtreeDoneLine)


def lines_of(tmp_path: Path, kind: type[_LINE]) -> list[_LINE]:
    """Every journal line of ``kind`` the run under ``tmp_path`` wrote, in file order.

    Read back off the REAL ``journal.jsonl`` rather than a spy on the port, so an arm that
    asserts a line was emitted is asserting the thing a later replay will actually read -
    through :func:`~agentdag.domain.journal.parse_journal_line`, which is what would catch a
    line this loop can write but nothing can read back.
    """
    run_dir = FsRunDir.open(tmp_path / "runs", "r1")
    return [line for line in JsonlJournal(run_dir.journal_path, run_dir.audit_path).lines() if isinstance(line, kind)]


def one_plan_entry_parent() -> Plan:
    """A parent whose single entry plans a subtree of its own."""
    return plan_with([entry(node_id="p", op="plan", args={"goal": "inner"})])


def unregistered_op_plan(goal: str) -> str:
    """A plan naming an op nothing registered, so the validator refuses it whole."""
    return json.dumps(
        {
            "goal": goal,
            "entries": [
                {
                    "spec": {"node_id": "x", "kind": "work", "deadline_s": 60.0},
                    "op": "teleport",
                    "args": {},
                    "brief": "beam it up",
                    "output_contract": ["status"],
                    "acceptance": None,
                }
            ],
            "done_when": {"ref": {"entry": "x", "field": "status"}, "op": "==", "value": "done"},
        }
    )


def never_done_plan(goal: str, node_id: str) -> str:
    """A plan whose entry lands cleanly and whose ``done_when`` still settles False.

    Nothing refutes here - the entry carries no acceptance - so the subtree runs to the end
    of its entries and reports the plan's OWN verdict, which is the thing
    :class:`~agentdag.domain.journal.SubtreeDoneLine` carries.
    """
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
            "done_when": {"ref": {"entry": node_id, "field": "status"}, "op": "==", "value": "failed"},
        }
    )


@pytest.mark.os_agnostic
def test_an_accepted_sub_plan_is_journaled_under_its_planner_s_own_key(tmp_path: Path) -> None:
    """Task 35 step 5. An accepted plan is an event of the run, not just a local branch.

    The key is the PLANNER dispatch's own journal key, which is what joins this line to that
    node's ``started`` and ``result`` lines: a reader asking "which dispatch produced this
    plan" has no other way back. ``node_id`` is the ``plan`` ENTRY, never an entry of the
    accepted plan - those get coordinator-allocated ids and appear in lines of their own.
    """
    executor = ReplanningExecutor(plans=[work_only_plan("inner", "w")])

    out = run_plan(tmp_path, one_plan_entry_parent(), executor=executor, planner=None)

    accepted = lines_of(tmp_path, PlanAcceptedLine)
    assert [(line.node_id, line.entries) for line in accepted] == [("p", 1)]
    assert accepted[0].key == out.records["p"].input_hash


@pytest.mark.os_agnostic
def test_a_refused_sub_plan_is_journaled_with_the_validator_s_reasons(tmp_path: Path) -> None:
    """Task 35 step 5. Without this line a refusal is indistinguishable from a broken planner.

    A refused sub-plan otherwise shows only as a planner node's DONE record and a subtree
    that never ran, with nothing in the journal saying the PLAN was rejected. The reasons go
    in verbatim, every one of them, for the reason the validator returns them all at once.
    """
    executor = ReplanningExecutor(plans=[unregistered_op_plan("inner")])

    out = run_plan(tmp_path, one_plan_entry_parent(), executor=executor, planner=None)

    invalidated = lines_of(tmp_path, PlanInvalidatedLine)
    assert [line.node_id for line in invalidated] == ["p"]
    assert invalidated[0].reasons == out.refused[0].reasons
    assert invalidated[0].key == out.records["p"].input_hash
    assert lines_of(tmp_path, PlanAcceptedLine) == []


@pytest.mark.os_agnostic
def test_a_re_planned_subtree_journals_one_accepted_line_per_plan_it_ran(tmp_path: Path) -> None:
    """Task 35 step 5, and the whole point of counting these lines.

    Two accepted plans under one node id is a subtree that was re-planned once, and that
    count is the cheap signal that a re-plan loop is churning. The KEYS must differ: each
    plan came from its own planner dispatch, and two lines sharing a key would say one
    dispatch produced both.
    """
    executor = ReplanningExecutor(plans=[always_refuting_plan("broken", "g2"), work_only_plan("fixed", "w")])

    run_plan(tmp_path, one_plan_entry_parent(), executor=executor, planner=None)

    accepted = lines_of(tmp_path, PlanAcceptedLine)
    assert [line.node_id for line in accepted] == ["p", "p"]
    assert len({line.key for line in accepted}) == 2


@pytest.mark.os_agnostic
def test_a_re_planned_subtree_s_verdict_is_keyed_to_the_plan_that_actually_ran(tmp_path: Path) -> None:
    """The claim ``_journal_subtree_done`` makes in prose, asserted: the LAST dispatch, not the first.

    A subtree that re-planned ran the plan its SECOND planner dispatch produced; the first
    plan was abandoned mid-flight. Keying the verdict to that abandoned dispatch would tie
    "this subtree finished" to a plan whose entries never completed, and every arm here
    passed with the key frozen at the first plan - which is what the rejected alternative
    (carrying the key down from where the plan was accepted) would have shipped.
    """
    executor = ReplanningExecutor(plans=[always_refuting_plan("broken", "g2"), work_only_plan("fixed", "w")])

    run_plan(tmp_path, one_plan_entry_parent(), executor=executor, planner=None)

    accepted = lines_of(tmp_path, PlanAcceptedLine)
    done_lines = lines_of(tmp_path, SubtreeDoneLine)
    assert len(accepted) == 2  # the arm is void if nothing was re-planned
    assert [(line.node_id, line.done) for line in done_lines] == [("p", True)]
    assert done_lines[0].key == accepted[1].key
    assert done_lines[0].key != accepted[0].key


@pytest.mark.os_agnostic
def test_a_settled_subtree_journals_its_own_done_verdict(tmp_path: Path) -> None:
    """Task 35 step 5. One line per subtree that reached a verdict, carrying the plan's own."""
    executor = ReplanningExecutor(plans=[work_only_plan("inner", "w")])

    out = run_plan(tmp_path, one_plan_entry_parent(), executor=executor, planner=None)

    done_lines = lines_of(tmp_path, SubtreeDoneLine)
    assert [(line.node_id, line.done) for line in done_lines] == [("p", True)]
    assert done_lines[0].key == out.records["p"].input_hash


@pytest.mark.os_agnostic
def test_a_subtree_whose_done_when_settles_false_journals_done_false(tmp_path: Path) -> None:
    """The direction control. Without it an implementation hard-coding ``done=True`` passes.

    Nothing refutes in this subtree - its entry lands DONE and carries no acceptance - so the
    only thing that can make the line False is the plan's own ``done_when``, which is exactly
    what the line claims to report.
    """
    executor = ReplanningExecutor(plans=[never_done_plan("inner", "w")])

    run_plan(tmp_path, one_plan_entry_parent(), executor=executor, planner=None)

    assert [(line.node_id, line.done) for line in lines_of(tmp_path, SubtreeDoneLine)] == [("p", False)]


@pytest.mark.os_agnostic
def test_replan_exhaustion_journals_the_subtree_as_not_done(tmp_path: Path) -> None:
    """A subtree that ran out of re-plans reached a verdict too, and it is not done.

    The parent turns the exhaustion into a refusal it can branch on; without this line the
    journal would show a run of accepted plans under one node id and then simply stop, with
    nothing saying how that subtree ended.
    """
    executor = ReplanningExecutor(plans=[always_refuting_plan("still broken", "g2")])

    run_plan(tmp_path, one_plan_entry_parent(), executor=executor, planner=None, run_limits=limits(max_replans=1))

    assert [(line.node_id, line.done) for line in lines_of(tmp_path, SubtreeDoneLine)] == [("p", False)]


@pytest.mark.os_agnostic
def test_a_plan_nobody_planned_journals_no_plan_lines_at_all(tmp_path: Path) -> None:
    """The absent case, decided rather than inherited: a hand-authored plan emits nothing.

    All three lines name a planner node and carry the journal key it was dispatched under,
    and a plan handed straight to :func:`execute_plan` has neither - no dispatch produced it,
    so there is no key, and its ``planner`` is ``None``, so there is no node to name. The
    schema requires both to be non-empty, so the honest answer is no line rather than an
    invented id.
    """
    executor = ReplanningExecutor(plans=[])

    run_plan(tmp_path, plan_with([entry(node_id="a")]), executor=executor, planner=None)

    assert lines_of(tmp_path, PlanAcceptedLine) == []
    assert lines_of(tmp_path, PlanInvalidatedLine) == []
    assert lines_of(tmp_path, SubtreeDoneLine) == []
