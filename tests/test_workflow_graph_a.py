"""Graph A on the kernel, end to end: suspend and approve, the crash window, replay purity.

Every adapter is the shipped one (git, the journal, the run directory, the lock, the
clock, the tier policy, the isolation scanner, the gate), over real git repositories in
a temporary directory. The only substitution is the executor, which is the one
genuinely external edge; see ``kernel_fakes``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from kernel_fakes import CommittingExecutor, StrayExecutor, git, launch, policy_path

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.context import Coordinator
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.application.kernel.replay import build_replay_index
from agentdag.application.workflows.graph_a import perform_push
from agentdag.domain.errors import SpecRejected
from agentdag.domain.graph_a import PushIntent, dedup_key
from agentdag.domain.journal import RunSummaryLine, StartedLine
from agentdag.domain.models import (
    Decision,
    ErrorType,
    NodeError,
    NodeOutcome,
    NodeStatus,
    RunStatus,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agentdag.application.kernel.ports import ExecutorRequest
    from agentdag.domain.journal import JournalLine


def journal_of(run_dir: FsRunDir) -> list[JournalLine]:
    """Read the run's journal back as typed lines."""
    return JsonlJournal(run_dir.journal_path, run_dir.audit_path).lines()


def started_keys(lines: list[JournalLine]) -> list[str]:
    """Every dispatch key the journal recorded a ``started`` line for, in file order."""
    return [line.key for line in lines if isinstance(line, StartedLine)]


def coordinator_over(run_dir: FsRunDir, tmp_path: Path) -> Coordinator:
    """Build a coordinator over an EXISTING run directory, with the real adapters.

    Used only to hand :func:`~agentdag.application.workflows.graph_a.perform_push` the git port
    and the worktree layout it reads. Nothing is dispatched through it, so the journal
    is untouched.
    """
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    return Coordinator(
        run_id=run_dir.root.name,
        workflow="graph-a",
        args={},
        dispatcher=Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock()),
        run_dir=run_dir,
        clock=UtcClock(),
        executors={},
        gate_port=MakeTestGate(lock=tmp_path / "gate.lock"),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=load_policy(policy_path()),
        parallel=1,
    )


class FailingWorkExecutor(CommittingExecutor):
    """Reports one node's work as FAILED, so its branch never reaches the gate or the scan."""

    def __init__(self, fail_on: str) -> None:
        """Bind the node id whose dispatch reports failure."""
        super().__init__()
        self.fail_on = fail_on

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Report a failed outcome for :attr:`fail_on`, and commit normally otherwise."""
        node_id = request.node_dir.parent.name
        if node_id != self.fail_on:
            return await super().run(request)
        self.calls.append(node_id)
        return NodeOutcome(
            status=NodeStatus.FAILED,
            key_facts={"turns": 1},
            typed_fields=["turns"],
            executor_used="claude",
            model_used=request.model,
            effort_used="-",
            error=NodeError(type=ErrorType.EXECUTOR_ERROR, message="nope", transient=False),
        )


@pytest.mark.os_agnostic
def test_graph_a_suspends_at_the_approve_then_a_decision_resumes_it_to_a_push(tmp_path: Path) -> None:
    executor = CommittingExecutor()

    outcome, run_dir = launch(tmp_path, executor)

    assert outcome.status == RunStatus.SUSPENDED
    assert outcome.suspended_node == "a_push_list"
    assert run_dir.read_state().status == RunStatus.SUSPENDED
    assert sorted(executor.calls) == ["w_migrate@0", "w_migrate@1"]

    run_dir.write_decision(Decision(node_id="a_push_list", decision="approve", by="tester", token_id="local"))
    outcome2, _ = launch(tmp_path, executor, resume="decision")

    assert outcome2.status == RunStatus.DONE
    assert sorted(executor.calls) == ["w_migrate@0", "w_migrate@1"]  # zero re-dispatch of the work
    origin = tmp_path / "scratch" / "origin" / "a.git"
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "HEAD", cwd=run_dir.worktree("a"))
    assert git("rev-parse", "main", cwd=tmp_path / "a") != git("rev-parse", "main", cwd=origin)
    index = build_replay_index(journal_of(run_dir))
    assert index.crash_window == set()
    assert index.decisions["a_push_list"].by == "tester"
    assert index.run_started is not None
    summary = [line for line in journal_of(run_dir) if isinstance(line, RunSummaryLine)][-1]
    assert summary.human_interactions == 1
    assert summary.tokens_by_row == {"sonnet": 30}


@pytest.mark.os_agnostic
def test_a_crash_between_started_and_result_resumes_by_redispatching_exactly_that_node(tmp_path: Path) -> None:
    # parallel=1 on purpose: with two branches in flight, the sibling's gate could be mid-thread
    # (started, no result) at the moment of the crash and the window would hold TWO keys by
    # construction, not by defect. Serial branches make "exactly one" exact.
    executor = CommittingExecutor(crash_on="w_migrate@1")

    with pytest.raises(SystemExit):
        launch(tmp_path, executor, parallel=1)

    run_dir = FsRunDir.open(tmp_path / "runs", "r1")
    index = build_replay_index(journal_of(run_dir))
    assert len(index.crash_window) == 1
    assert run_dir.read_state().status in (RunStatus.RUNNING, RunStatus.CRASHED)

    second = CommittingExecutor()
    outcome, _ = launch(tmp_path, second, resume="crash")

    assert second.calls == ["w_migrate@1"]  # exactly the crashed node, once
    assert outcome.status == RunStatus.SUSPENDED


@pytest.mark.os_agnostic
def test_replay_of_a_finished_run_dispatches_nothing_and_reproduces_the_key_sequence(tmp_path: Path) -> None:
    executor = CommittingExecutor()
    launch(tmp_path, executor)
    run_dir = FsRunDir.open(tmp_path / "runs", "r1")
    run_dir.write_decision(Decision(node_id="a_push_list", decision="hold", by="tester", token_id="local"))
    launch(tmp_path, executor, resume="decision")
    lines_before = journal_of(run_dir)

    outcome, _ = launch(tmp_path, executor, resume="manual")

    lines_after = journal_of(run_dir)
    assert outcome.status == RunStatus.DONE
    assert len(executor.calls) == 2
    assert started_keys(lines_after) == started_keys(lines_before)
    # Replay purity: the same keys, the same count. The ORDER of two parallel map branches is
    # scheduling-dependent by construction (a real map, not a defect), so order is compared as a
    # multiset here; within a chain the order is enforced by the key itself (a dependent's key
    # embeds its dep's record hash), and Task 12's serial test checks it exactly.
    assert run_dir.read_state().cursor is None
    assert sorted(outcome.dispatched_keys) == sorted(started_keys(lines_before))
    assert len(outcome.dispatched_keys) == len(started_keys(lines_before))


@pytest.mark.os_agnostic
def test_a_stray_write_into_an_undeclared_worktree_fails_the_scan_and_the_branch(tmp_path: Path) -> None:
    outcome, run_dir = launch(tmp_path, StrayExecutor())

    tally = (run_dir.root / "artefacts" / "tally.json").read_text(encoding="utf-8")
    assert outcome.status == RunStatus.DONE
    assert '"passed": 0' in tally  # nothing pushable, nobody asked
    assert not list((run_dir.root / "intents").glob("push/*.json"))


@pytest.mark.os_agnostic
def test_a_branch_whose_work_node_failed_tallies_and_never_reaches_the_push_list(tmp_path: Path) -> None:
    outcome, run_dir = launch(tmp_path, FailingWorkExecutor("w_migrate@0"))

    tally = json.loads((run_dir.root / "artefacts" / "tally.json").read_text(encoding="utf-8"))
    assert [row["status"] for row in tally["rows"]] == ["work-failed", "passed"]
    assert tally["passed"] == 1
    # r_tally depends on each branch's LAST node, which for a failed branch is its work node,
    # not the gate and scan it never dispatched. A wrong dep list raises KernelError instead.
    manifest = json.loads((run_dir.root / "manifest" / "m_migrate.json").read_text(encoding="utf-8"))
    assert [branch["node_id"] for branch in manifest["branches"]] == ["w_migrate@0", "g_scan@1"]
    assert outcome.status == RunStatus.SUSPENDED
    staged = sorted(path.name for path in (run_dir.root / "intents" / "push").iterdir())
    assert len(staged) == 1 and staged[0].startswith("b.git-")


@pytest.mark.os_agnostic
def test_a_push_whose_target_already_points_at_the_commit_is_reported_not_repeated(tmp_path: Path) -> None:
    # The already-present branch cannot be reached through one program run: within a launch the
    # done marker short-circuits it, and across launches the apply node is served from the
    # journal. So the guard is exercised directly, against a real origin the run really pushed to.
    executor = CommittingExecutor()
    _, run_dir = launch(tmp_path, executor)
    run_dir.write_decision(Decision(node_id="a_push_list", decision="approve", by="tester", token_id="local"))
    launch(tmp_path, executor, resume="decision")
    origin = tmp_path / "scratch" / "origin" / "a.git"
    head = git("rev-parse", "main", cwd=origin)
    co = coordinator_over(run_dir, tmp_path)
    intent = PushIntent(repo=origin, head_sha=head, dedup_key=dedup_key(origin, head))

    outcome = perform_push(co, tmp_path / "scratch", intent)

    # The REF is read, not the object: a push whose objects transferred and whose ref update was
    # rejected leaves the commit present while the branch points elsewhere, and calling that
    # already-present would abandon the push forever.
    assert outcome == "already-present"
    assert git("rev-parse", "main", cwd=origin) == head
    real = tmp_path / "a"
    with pytest.raises(SpecRejected):
        perform_push(co, tmp_path / "scratch", PushIntent(repo=real, head_sha=head, dedup_key=dedup_key(real, head)))
