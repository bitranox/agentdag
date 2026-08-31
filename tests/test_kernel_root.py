"""Task 35 step 4b: the ROOT plan has no parent, so it re-plans and then asks a person.

A nested plan the validator refuses becomes a FAILED record its parent branches on. The root
has nobody to report to, so it takes the ladder the rest of this project already uses for
"retry, then ask": re-dispatch the root planner with the validator's reasons, bounded by
``max_replans``, and on exhaustion SUSPEND into ``approve`` rather than failing - a suspended
run stays resumable and keeps every record it earned.

Every arm drives :func:`~agentdag.application.kernel.root.run_root` against a REAL coordinator
over a real run directory, with a fake only at the executor port, the same shape
``test_kernel_replan.py`` uses. The planner WRITES a plan.json exactly as production's does, so
the parse and the validator - the two things most likely to reject what a real planner writes -
are in the loop rather than stubbed past.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING

import pytest
from kernel_fakes import FakeScanner, RedGate, fresh_run_dir, outcome, policy_path, wire

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.execute import NodeBudget, NodeIds
from agentdag.application.kernel.registry import PlanContext
from agentdag.application.kernel.root import run_root
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.workflows import get_workflow
from agentdag.application.workflows.plan_goal import PlanGoalArgs
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.journal import PlanAcceptedLine, PlanInvalidatedLine, RunStartedLine
from agentdag.domain.kernel_errors import Suspended
from agentdag.domain.keys import hash8
from agentdag.domain.models import ApprovePayload, Budget, Decision, Kind, NodeSpec, RunStatus, TierRole
from agentdag.domain.policy import RunLimits

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from agentdag.application.kernel.execute import Executed
    from agentdag.application.kernel.ports import ExecutorRequest
    from agentdag.domain.models import NodeOutcome

REG = build_op_registry()

PLANNER_ID = "p_root"
APPROVE_ID = "a_root_planning"


def limits(*, max_replans: int = 3) -> RunLimits:
    """Run limits generous everywhere but the one bound an arm names."""
    return RunLimits(
        tokens_per_row={"sonnet": 1_000_000_000},
        deadline_ceiling_s=999_999.0,
        per_kind_ceiling={},
        planner_kinds=[],
        top_role_budget_floor=0.0,
        max_replans=max_replans,
        max_nodes_per_run=1000,
        max_nodes_per_plan=1000,
        max_plan_depth=5,
    )


def planner_spec() -> NodeSpec:
    """The root planner the workflow program passes in; ``run_root`` mints nothing."""
    return NodeSpec(
        node_id=PLANNER_ID,
        kind=Kind.PLANNER,
        tier_role=TierRole.TOP,
        deadline_s=600,
        budget=Budget(tokens={"sonnet": 400_000}),
    )


def approve_spec() -> NodeSpec:
    """The human gate the ladder ends at; its deadline IS the decision window."""
    return NodeSpec(node_id=APPROVE_ID, kind=Kind.APPROVE, executor="code", deadline_s=3600)


def valid_plan(goal: str = "g", node_id: str = "repair") -> str:
    """One plan a root may run: a single ``work`` entry, whose completion is ``done_when``."""
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


def refuted_condition_plan(goal: str = "g", *, work_id: str = "repair", gate_id: str = "g1") -> str:
    """A root plan the validator ACCEPTS and a RED gate then refutes.

    The two entries do different jobs. The ``work`` one is what carries ``done_when``, and it
    is state-changing, so the root-only rule that a completion condition may not rest on a
    gate's exit code alone (``_requires_state_change``) is satisfied and this plan REACHES
    execution. The gate's own ``acceptance`` is what a :class:`RedGate` then refutes. Without
    the work entry the validator would refuse the plan and every arm below would be testing
    the OTHER ladder - the one for plans that never ran.
    """
    return json.dumps(
        {
            "goal": goal,
            "entries": [
                {
                    "spec": {"node_id": work_id, "kind": "work", "deadline_s": 60.0},
                    "op": "work",
                    "args": {},
                    "brief": f"do {work_id}",
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
            "done_when": {"ref": {"entry": work_id, "field": "status"}, "op": "==", "value": "done"},
        }
    )


def unregistered_op_plan(goal: str = "g", op: str = "teleport") -> str:
    """A plan the validator refuses by ABSENCE: nothing registers ``op``."""
    return json.dumps(
        {
            "goal": goal,
            "entries": [
                {
                    "spec": {"node_id": "x", "kind": "work", "deadline_s": 60.0},
                    "op": op,
                    "args": {},
                    "brief": "beam it over",
                    "output_contract": ["status"],
                    "acceptance": None,
                }
            ],
            "done_when": {"ref": {"entry": "x", "field": "status"}, "op": "==", "value": "done"},
        }
    )


class RootPlanningExecutor:
    """Writes one plan per planner dispatch, from a list, and records every brief.

    The last entry repeats, so ``plans=[invalid]`` is a planner that never gets it right.
    """

    def __init__(self, *, plans: Sequence[str]) -> None:
        """Start with nothing dispatched; ``plans`` is what each planner dispatch writes."""
        self.briefs: list[str] = []
        self.planner_dispatches = 0
        self._plans = list(plans)

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Write this planner's plan, or just record an ordinary node's dispatch."""
        if "You are a planner" in request.prompt:
            self.briefs.append(request.brief)
            nth = self.planner_dispatches
            self.planner_dispatches += 1
            raw = self._plans[nth] if nth < len(self._plans) else self._plans[-1]
            (request.node_dir / "plan.json").write_text(raw, encoding="utf-8")
        return outcome({"sonnet": 10})


def root_run_dir(tmp_path: Path) -> FsRunDir:
    """A fresh run directory carrying the ``run_started`` line every real run opens with.

    Not decoration: the exhaustion approve's deadline is measured from that line, so that a
    relaunch asks the SAME question rather than a new one a second later, and a run directory
    without it is one ``run_coordinator`` could never produce.
    """
    run_dir = fresh_run_dir(tmp_path)
    JsonlJournal(run_dir.journal_path, run_dir.audit_path).append(
        RunStartedLine(
            at="2026-08-30T09:00:00+00:00",
            run_id="r1",
            workflow="t",
            args={},
            by="tester",
            token_id="local",
            policy_version="v1",
        )
    )
    return run_dir


def drive(
    run_dir: FsRunDir,
    executor: RootPlanningExecutor,
    *,
    goal: str = "g",
    run_limits: RunLimits | None = None,
    gate_port: RedGate | None = None,
) -> Executed:
    """Run the root ladder over ``run_dir``, as a relaunch would over an existing one.

    ``gate_port`` defaults to the coordinator's own, which is the REAL ``make test`` gate; an
    arm whose plan carries a gate entry must pass a fake, or the fixture shells out.
    """
    coordinator = wire(run_dir, executor, FakeScanner(), gate_port=gate_port)  # type: ignore[arg-type]
    coordinator.fold_decisions()
    ctx = PlanContext(co=coordinator, cwd=run_dir.worktree("a"))
    return asyncio.run(
        run_root(
            goal=goal,
            planner=planner_spec(),
            approve=approve_spec(),
            ctx=ctx,
            registry=REG,
            limits=run_limits or limits(),
            graph={},
            spent=NodeBudget(),
            ids=NodeIds(),
        )
    )


def payload_on_offer(run_dir: FsRunDir, payload_hash: str) -> ApprovePayload:
    """Read back the payload the suspend just published, where the decider reads it."""
    path = run_dir.node_dir(APPROVE_ID, hash8(payload_hash)) / "payload.json"
    return ApprovePayload.model_validate_json(path.read_text(encoding="utf-8"))


def answer(run_dir: FsRunDir, payload_hash: str, verdict: str) -> None:
    """Record a human's decision against the exact payload the run suspended on."""
    run_dir.write_decision(
        Decision(node_id=APPROVE_ID, decision=verdict, by="tester", token_id="local", payload_hash=payload_hash)
    )


@pytest.mark.os_agnostic
def test_a_root_plan_the_validator_refuses_is_re_planned_with_the_reasons(tmp_path: Path) -> None:
    """Step 4b. The root has no parent to report a refusal to, so it re-plans itself.

    Asserted on the REASONS reaching the second brief, not merely on a second dispatch: a
    planner re-asked for the same goal with no word of what was wrong writes the same plan
    again, and the bound then buys nothing but spend.
    """
    executor = RootPlanningExecutor(plans=[unregistered_op_plan(), valid_plan()])

    out = drive(root_run_dir(tmp_path), executor)

    assert executor.planner_dispatches == 2
    assert "teleport" in executor.briefs[1]
    assert out.done is True


@pytest.mark.os_agnostic
def test_a_valid_root_plan_never_reaches_the_approve(tmp_path: Path) -> None:
    """The control. Without it an implementation that suspends unconditionally passes every
    arm below, and every run would stop for a human on its first plan."""
    run_dir = root_run_dir(tmp_path)
    executor = RootPlanningExecutor(plans=[valid_plan()])

    out = drive(run_dir, executor)

    assert executor.planner_dispatches == 1
    assert out.done is True
    assert not list(run_dir.root.glob(f"nodes/{APPROVE_ID}/*/payload.json"))


@pytest.mark.os_agnostic
def test_root_planning_exhaustion_suspends_rather_than_failing(tmp_path: Path) -> None:
    """Step 4b. Exhaustion asks a person; it does not take the run down.

    ``max_replans=1`` buys one re-dispatch, so exactly two planner dispatches precede the
    suspend. The suspend carries the payload hash, because a decision is recorded per (node
    id, payload hash) and nothing else says WHICH question was asked.
    """
    run_dir = root_run_dir(tmp_path)
    executor = RootPlanningExecutor(plans=[unregistered_op_plan()])

    with pytest.raises(Suspended) as info:
        drive(run_dir, executor, run_limits=limits(max_replans=1))

    assert info.value.node_id == APPROVE_ID
    assert info.value.payload_hash is not None
    assert executor.planner_dispatches == 2


@pytest.mark.os_agnostic
def test_the_exhaustion_payload_offers_abandon_by_default_and_names_the_reasons(tmp_path: Path) -> None:
    """The decider is asked a question they can answer: what went wrong, and the two ways out.

    ``abandon`` is the DEFAULT because a default is what the deadline owner applies
    unattended, so it may never be the option that spends (design 2.4).
    """
    run_dir = root_run_dir(tmp_path)
    executor = RootPlanningExecutor(plans=[unregistered_op_plan()])

    with pytest.raises(Suspended) as info:
        drive(run_dir, executor, run_limits=limits(max_replans=1))

    payload = payload_on_offer(run_dir, str(info.value.payload_hash))
    assert payload.default == "abandon"
    assert {option.id for option in payload.options} == {"abandon", "replan"}
    assert next(o for o in payload.options if o.id == "abandon").effect == "none"
    # Both are "none": the schema defines "external" as an effect that LEAVES the process
    # ("a push, a publish, a mail"), and re-planning spends inside the run. Pinned because
    # mislabelling it would also pull this gate into O21's distinct-human-identity rule.
    assert next(o for o in payload.options if o.id == "replan").effect == "none"
    assert "teleport" in payload.text


@pytest.mark.os_agnostic
def test_the_exhaustion_payload_points_at_the_planner_dispatch_that_failed(tmp_path: Path) -> None:
    """``artefact_refs`` names the FAILING dispatch's node directory, and that path exists.

    Two jobs at once: the decider gets a pointer to what the planner actually wrote, and the
    payload carries a field that differs per round, which is what keeps a repeated ask from
    hashing the same (see the arm below).
    """
    run_dir = root_run_dir(tmp_path)
    executor = RootPlanningExecutor(plans=[unregistered_op_plan()])

    with pytest.raises(Suspended) as info:
        drive(run_dir, executor, run_limits=limits(max_replans=1))

    payload = payload_on_offer(run_dir, str(info.value.payload_hash))
    assert len(payload.artefact_refs) == 1
    assert payload.artefact_refs[0].startswith(f"nodes/{PLANNER_ID}/")
    assert (run_dir.root / payload.artefact_refs[0] / "plan.json").exists()


@pytest.mark.os_agnostic
def test_repeated_exhaustions_never_share_a_payload_hash(tmp_path: Path) -> None:
    """The arm the whole ladder rests on, and nothing else would catch its loss.

    A decision is FINAL per (node id, payload hash). If a granted round exhausted under a
    payload that hashed the SAME, the recorded grant would be re-served instead of asked
    again, and the run would re-plan unattended forever - the exact shape the retry option
    was added to avoid. So every exhaustion must ask a question of its own, however many
    rounds a person grants.

    THREE rounds, not two, and the third is the one that does the work. A second round comes
    out distinct even with the distinguishing mechanism removed, because the approve node's
    OWN record joins the planner's evidence the first time a decision is served and changes
    the next brief once, for free. Only from the third does the ladder need the mechanism it
    is supposed to rely on - measured 2026-08-30, where a two-round arm passed against an
    implementation that loops.
    """
    run_dir = root_run_dir(tmp_path)
    seen: list[str] = []

    for _ in range(3):
        with pytest.raises(Suspended) as info:
            drive(run_dir, RootPlanningExecutor(plans=[unregistered_op_plan()]), run_limits=limits(max_replans=1))
        seen.append(str(info.value.payload_hash))
        answer(run_dir, seen[-1], "replan")

    assert len(set(seen)) == 3, "an exhaustion re-asked a question whose answer is already recorded"


@pytest.mark.os_agnostic
def test_an_abandoned_root_reports_the_refusal_instead_of_a_done_subtree(tmp_path: Path) -> None:
    """``abandon`` is not a failure and not a success: it is a subtree nobody could plan.

    The reasons travel on ``refused`` verbatim, which is where a caller already looks for a
    plan the validator would not take.
    """
    run_dir = root_run_dir(tmp_path)
    executor = RootPlanningExecutor(plans=[unregistered_op_plan()])

    with pytest.raises(Suspended) as info:
        drive(run_dir, executor, run_limits=limits(max_replans=1))
    answer(run_dir, str(info.value.payload_hash), "abandon")

    out = drive(run_dir, RootPlanningExecutor(plans=[unregistered_op_plan()]), run_limits=limits(max_replans=1))

    assert out.done is False
    assert [r.node_id for r in out.refused] == [PLANNER_ID]
    assert any("teleport" in reason for reason in out.refused[0].reasons)


@pytest.mark.os_agnostic
def test_a_granted_round_that_finally_plans_runs_the_plan_it_produced(tmp_path: Path) -> None:
    """The control for the grant. Without it, ``replan`` could suspend again forever and the
    arm above would still pass - a retry that can never succeed is not a retry."""
    run_dir = root_run_dir(tmp_path)
    with pytest.raises(Suspended) as info:
        drive(run_dir, RootPlanningExecutor(plans=[unregistered_op_plan()]), run_limits=limits(max_replans=1))
    answer(run_dir, str(info.value.payload_hash), "replan")

    out = drive(
        run_dir,
        RootPlanningExecutor(plans=[valid_plan()]),
        run_limits=limits(max_replans=1),
    )

    assert out.done is True
    assert out.refused == ()


@pytest.mark.os_agnostic
def test_the_root_planner_s_dispatches_are_journaled_like_any_other(tmp_path: Path) -> None:
    """A plan nobody planned emits no plan lines; the root IS planned, so it emits them.

    One invalidated line per refused plan and one accepted line for the plan that ran, each
    keyed to the planner dispatch that produced it.
    """
    run_dir = root_run_dir(tmp_path)
    executor = RootPlanningExecutor(plans=[unregistered_op_plan(), valid_plan()])

    drive(run_dir, executor)

    lines = [json.loads(raw) for raw in run_dir.journal_path.read_text(encoding="utf-8").splitlines()]
    invalidated = [line for line in lines if line["event"] == PlanInvalidatedLine.model_fields["event"].default]
    accepted = [line for line in lines if line["event"] == PlanAcceptedLine.model_fields["event"].default]
    assert [line["node_id"] for line in invalidated] == [PLANNER_ID]
    assert any("teleport" in reason for reason in invalidated[0]["reasons"])
    assert [line["node_id"] for line in accepted] == [PLANNER_ID]


class PlanThenWorkExecutor:
    """Writes a plan for a planner node and reports DONE for everything else.

    The end-to-end arm's whole point is that nothing is stubbed between the workflow
    program and the plan that runs, so this sits at the same executor port production's
    Claude executor sits at.
    """

    def __init__(self, raw: str) -> None:
        """Start with nothing dispatched; ``raw`` is what every planner dispatch writes."""
        self.dispatched: list[str] = []
        self.raw = raw

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Write the plan when this is a planner dispatch, and record what ran."""
        self.dispatched.append(request.node_dir.parent.name)
        if "You are a planner" in request.prompt:
            (request.node_dir / "plan.json").write_text(self.raw, encoding="utf-8")
        return outcome({"sonnet": 10})


@pytest.mark.os_agnostic
def test_a_workflow_program_can_reach_run_root_through_the_coordinator(tmp_path: Path) -> None:
    """The wiring, end to end: ``agentdag run start plan-goal --arg goal=...`` reaches the ladder.

    Driven through :func:`~agentdag.application.kernel.run.run_coordinator` rather than by
    calling ``run_root`` directly, because what is under test is precisely what a program is
    HANDED: it gets ``(co, args)`` and nothing else, so the op registry and the run limits
    have to arrive on the coordinator or the ladder has no production caller at all.
    """
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "r1")
    executor = PlanThenWorkExecutor(valid_plan(goal="tidy the docs", node_id="repair"))

    outcome_of_run = asyncio.run(
        run_coordinator(
            run_dir=run_dir,
            journal=JsonlJournal(run_dir.journal_path, run_dir.audit_path),
            clock=UtcClock(),
            lock=FileRunLock(),
            holder=current_holder(),
            workflow=get_workflow("plan-goal"),
            args=PlanGoalArgs(goal="tidy the docs"),
            executors={"claude": executor},
            gate_port=MakeTestGate(command=(sys.executable, "-c", "raise SystemExit(0)")),
            git=GitCli(),
            scanner=IsolationScanner(),
            policy=load_policy(policy_path()),
            sandbox=NoSandbox(),
            registry=REG,
            parallel=2,
            by="tester",
            token_id="local",
            resume_reason=None,
            notifier=NoNotifier(),
        )
    )

    assert outcome_of_run.status is RunStatus.DONE
    assert any(node_id.startswith("n-") for node_id in executor.dispatched), "the planned entry never ran"


@pytest.mark.os_agnostic
def test_the_policy_port_carries_the_whole_run_limits(tmp_path: Path) -> None:
    """A workflow program reaches its run limits through ``co.policy``, so the PORT must carry
    them - all of them, not the two fields the dispatch path happened to need.

    ``max_replans`` is the one this ladder binds on and the one a partial port could not
    supply; asserted against the shipped ``tier-policy.yaml`` rather than a fixture, because
    what is being checked is that the loaded table reaches the port intact.
    """
    del tmp_path
    policy = load_policy(policy_path())

    assert policy.run_limits.max_replans >= 0
    assert policy.run_limits.max_nodes_per_run > 0
    assert policy.run_limits.deadline_ceiling_s > 0


@pytest.mark.os_agnostic
def test_a_root_plan_whose_condition_refutes_past_the_allowance_asks_rather_than_failing(
    tmp_path: Path,
) -> None:
    """The root's OTHER exhaustion: the plan was accepted, ran, and its condition refuted.

    Until this arm that path raised ``ReplanLimitExceededError`` out of ``execute_plan``, so
    the run ended FAILED and unresumable - and it strands the EXPENSIVE records, the ones a
    plan that actually RAN produced. The plans here all validate, so nothing on this path can
    reach the ladder for plans the validator refuses; what exhausts is the re-plan allowance
    inside ``execute_plan``.
    """
    run_dir = root_run_dir(tmp_path)
    executor = RootPlanningExecutor(plans=[refuted_condition_plan()])

    with pytest.raises(Suspended) as info:
        drive(run_dir, executor, run_limits=limits(max_replans=1), gate_port=RedGate())

    assert info.value.node_id == APPROVE_ID
    assert info.value.payload_hash is not None


@pytest.mark.os_agnostic
def test_a_granted_round_buys_another_whole_max_replans(tmp_path: Path) -> None:
    """The user's decision, 2026-08-31: a grant buys another ``max_replans``, not one attempt.

    ``max_replans=2`` is what makes the arm able to tell those apart - at 1 they are the same
    number and an implementation granting a single attempt passes. Counted on the RESUMED
    launch, where every dispatch the first launch made is served from the journal, so what
    the executor sees run is exactly what the grant bought.
    """
    run_dir = root_run_dir(tmp_path)
    first = RootPlanningExecutor(plans=[refuted_condition_plan()])

    with pytest.raises(Suspended) as info:
        drive(run_dir, first, run_limits=limits(max_replans=2), gate_port=RedGate())
    answer(run_dir, str(info.value.payload_hash), "replan")

    granted = RootPlanningExecutor(plans=[refuted_condition_plan()])
    with pytest.raises(Suspended):
        drive(run_dir, granted, run_limits=limits(max_replans=2), gate_port=RedGate())

    assert first.planner_dispatches == 3, "the opening plan plus max_replans re-plans"
    assert granted.planner_dispatches == 2, "a granted round must buy max_replans, not one attempt"


@pytest.mark.os_agnostic
def test_an_abandoned_refutation_reports_the_records_it_earned(tmp_path: Path) -> None:
    """Abandon must REPORT, never raise: the point of the ladder is that the run stays
    resumable and keeps the expensive records a plan that actually ran produced.

    The ``cause`` is the field that says why it stopped, and it is what distinguishes this
    from the abandon on the validator ladder - that one has reasons and no cause, because
    nothing ever ran.
    """
    run_dir = root_run_dir(tmp_path)
    with pytest.raises(Suspended) as info:
        drive(
            run_dir,
            RootPlanningExecutor(plans=[refuted_condition_plan()]),
            run_limits=limits(max_replans=1),
            gate_port=RedGate(),
        )
    answer(run_dir, str(info.value.payload_hash), "abandon")

    out = drive(
        run_dir,
        RootPlanningExecutor(plans=[refuted_condition_plan()]),
        run_limits=limits(max_replans=1),
        gate_port=RedGate(),
    )

    assert out.done is False
    assert out.cause is not None, "an abandoned refutation must say which condition stopped it"
    assert any(record.node_id.startswith("n-") for record in out.records.values()), "records were thrown away"


@pytest.mark.os_agnostic
def test_every_re_plan_brief_is_distinct_so_a_granted_round_is_a_real_dispatch(tmp_path: Path) -> None:
    """The property the whole ladder rests on, pinned on the mechanism that supplies it.

    A dispatch briefed to the identical word is SERVED from the resumed launch's replay index
    rather than run, so if two rounds could brief the planner the same way, a grant would
    replay the dispatch the decider had just read and buy nothing.

    Nothing enforces that directly, which is why it is pinned here rather than trusted. Two
    renderings supply it independently - the refuting node id and values in the goal text,
    and the evidence block the planner brief appends - and both reduce to node ids being
    allocated fresh per accepted plan. Verified by mutating BOTH: removing either alone
    leaves this arm green, and only with both gone do the three briefs collapse into one.
    """
    run_dir = root_run_dir(tmp_path)
    executor = RootPlanningExecutor(plans=[refuted_condition_plan()])

    with pytest.raises(Suspended):
        drive(run_dir, executor, run_limits=limits(max_replans=3), gate_port=RedGate())

    re_plans = [brief for brief in executor.briefs if "was stopped" in brief]
    assert len(re_plans) == 3, "max_replans=3 must brief the planner three times over"
    assert len(set(re_plans)) == 3, "two rounds briefed the planner identically, so one was served not run"


@pytest.mark.os_agnostic
def test_each_granted_round_asks_a_question_of_its_own(tmp_path: Path) -> None:
    """Termination. A decision is FINAL per (node id, payload hash), so an exhaustion that
    rebuilt an identical payload would have the recorded grant RE-SERVED instead of asked,
    and the ladder would re-plan unattended forever.

    THREE rounds, not two, for the reason the sibling arm on the validator ladder runs three:
    a second round comes out distinct even with the distinguishing mechanism gone, because
    the approve node's OWN record joins the planner's evidence the first time a decision is
    served and changes the next brief once, for free.
    """
    run_dir = root_run_dir(tmp_path)
    seen: list[str] = []

    for _ in range(3):
        with pytest.raises(Suspended) as info:
            drive(
                run_dir,
                RootPlanningExecutor(plans=[refuted_condition_plan()]),
                run_limits=limits(max_replans=1),
                gate_port=RedGate(),
            )
        seen.append(str(info.value.payload_hash))
        answer(run_dir, seen[-1], "replan")

    assert len(set(seen)) == 3, "an exhaustion re-asked a question whose answer is already recorded"
