"""Shared fakes for the kernel's end-to-end tests: real git, a committing executor, a fleet.

Everything a graph A run touches is REAL here except the one genuinely external edge,
the model call: the journal, the run directory, the clock, the lock, the tier policy,
the isolation scanner, the gate and git are the shipped adapters, and only the
:class:`~agentdag.application.kernel.ports.Executor` port is substituted. The
substitute still does what a work node does - it edits a file in its working
directory and commits - so the gate, the scan and the push all have real content to
judge.

This module is imported by ``test_workflow_graph_a.py``, ``test_kernel_run.py`` and
the CLI tests, so a change to how a run is launched is made once.

Contents:
    * :func:`git` - run one git command and return its stdout.
    * :func:`make_repo` - a one-commit repository with a green ``make test``.
    * :class:`CommittingExecutor` - the work-node stand-in; optionally crashes the process.
    * :class:`StrayExecutor` - writes outside every declared write set, so the scan fails.
    * :class:`RecordingNotifier` - a notification sink that keeps every event it is given.
    * :func:`fleet` - mirror N repositories into a scratch tree and build the run's args.
    * :func:`policy_path` - the shipped tier policy table.
    * :func:`launch` - start or resume one graph A run over a real run directory.
    * :func:`decide` - answer the exact payload a suspended run is waiting on.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GIT_EXECUTABLE, GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.workflows import get_workflow
from agentdag.application.workflows.graph_a import GraphAArgs
from agentdag.domain.models import Decision, ErrorType, NodeError, NodeOutcome, NodeStatus, Tokens

if TYPE_CHECKING:
    from agentdag.application.kernel.notify import Notifier, RunEvent
    from agentdag.application.kernel.ports import ExecutorRequest
    from agentdag.application.kernel.run import RunOutcome

from agentdag.application.kernel.context import Coordinator
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.application.kernel.ports import ResolvedRow
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.models import Budget, Isolation, Kind, NodeSpec, TierRole
from agentdag.domain.plan import PLAN_FILENAME
from agentdag.domain.policy import FailureAction, RunLimits

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentdag.application.graph_a_ports import GatePort
    from agentdag.application.kernel.ports import Executor

__all__ = [
    "CommittingExecutor",
    "RecordingNotifier",
    "RedGate",
    "StrayExecutor",
    "decide",
    "fleet",
    "git",
    "launch",
    "make_repo",
    "policy_path",
]


class RedGate:
    """A gate port that runs to completion and reports a real, non-zero answer.

    A RED gate rather than a broken one: the mechanical step ran and said no, which is an
    ordinary ``failed`` record with ``rc`` in ``key_facts`` - the thing a plan's
    ``acceptance`` is written to refute. Shared here because two suites need it (the retry
    table in ``test_kernel_context.py`` and the condition checks in
    ``test_kernel_execute.py``), and a second copy would be free to drift from the first.
    """

    def __init__(self) -> None:
        """Start with no calls recorded."""
        self.calls = 0

    def run(self, worktree: Path, log: Path) -> int:
        """Count the call and report a red gate."""
        del worktree, log
        self.calls += 1
        return 1


def git(*args: str, cwd: Path) -> str:
    """Run one git command in ``cwd`` and return its stripped stdout.

    Args:
        args: The git arguments, without the leading ``git``.
        cwd: The directory to run in.

    Returns:
        The command's stdout, stripped.
    """
    completed = subprocess.run(  # noqa: S603 - a fixed argument list, never a shell string
        [GIT_EXECUTABLE, *args],  # absolute: Windows CreateProcess searches the PARENT PATH
        cwd=cwd,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def make_repo(root: Path, name: str) -> Path:
    """Create ``root/name`` as a one-commit repository whose ``make test`` is green.

    Args:
        root: Where the repository directory is created.
        name: The repository's directory name; also its fleet member name.

    Returns:
        The repository path.
    """
    repo = root / name
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@example.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "Makefile").write_text("test:\n\t@exit 0\n", encoding="utf-8")
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    return repo


class CommittingExecutor:
    """The work node's stand-in: edits a file in its working directory and commits it.

    The body is idempotent, which the crash window requires of any real node: a
    re-dispatch after a crash that landed the commit finds a clean tree and commits
    nothing a second time. Committing unconditionally would instead abort on git's
    "nothing to commit" exit code and turn a node that SUCCEEDED into a failed one.

    Attributes:
        crash_on: A node id whose dispatch raises ``SystemExit`` BEFORE doing anything -
            the coordinator PROCESS dying between the ``started`` line and any side
            effect.
        crash_after: A node id whose dispatch raises ``SystemExit`` AFTER its commit -
            the harder half of the crash window, where the side effect landed and only
            the ``result`` line is missing.
        fail_on: A node id whose dispatch returns a FAILED outcome rather than raising -
            a node that ran and reported it could not do the job, which is what leaves a
            failed RECORD in the journal for a later ``run retry`` to be granted against.
            Cleared once it has fired, so the attempt an operator grants succeeds and a
            test can tell a retry that ran from one that never happened.
        calls: The node id of every dispatch this executor was handed, in order.
    """

    def __init__(self, crash_on: str | None = None, crash_after: str | None = None, fail_on: str | None = None) -> None:
        """Bind the optional crash and failure nodes; ``calls`` starts empty."""
        self.crash_on = crash_on
        self.crash_after = crash_after
        self.fail_on = fail_on
        self.calls: list[str] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Commit one change in ``request.cwd`` and report a done outcome.

        Args:
            request: The dispatch request; its ``node_dir``'s parent names the node.

        Returns:
            A done outcome carrying one artefact ref and typed ``turns``.

        Raises:
            SystemExit: this dispatch's node id is :attr:`crash_on` or :attr:`crash_after`.
        """
        node_id = request.node_dir.parent.name
        self.calls.append(node_id)
        if node_id == self.crash_on:
            raise SystemExit(9)
        if node_id == self.fail_on:
            self.fail_on = None
            return NodeOutcome(
                status=NodeStatus.FAILED,
                executor_used="claude",
                model_used=request.model,
                effort_used="-",
                error=NodeError(type=ErrorType.EXECUTOR_ERROR, message="the model would not do it", transient=True),
            )
        # A real executor awaits its model; without a suspension point here every map
        # branch would run to completion before the next one started, so `parallel > 1`
        # would be serial and nothing that depends on branches overlapping is exercised.
        await asyncio.sleep(0)
        (request.cwd / "CHANGELOG.md").write_text(request.brief + "\n", encoding="utf-8")
        if git("status", "--porcelain", cwd=request.cwd):
            git("add", "-A", cwd=request.cwd)
            git("commit", "-q", "-m", "change", cwd=request.cwd)
        if node_id == self.crash_after:
            raise SystemExit(9)
        return NodeOutcome(
            status=NodeStatus.DONE,
            key_facts={"turns": 1},
            typed_fields=["turns"],
            artefact_refs=[request.cwd.relative_to(request.isolation_root).as_posix()],
            tokens=Tokens(**{"in": 10, "out": 5, "cache_read": 0, "reasoning": None}),
            charged_tokens={request.model: 15},
            executor_used="claude",
            model_used=request.model,
            effort_used="-",
        )


class StrayExecutor(CommittingExecutor):
    """Commits like its parent, and also writes into a worktree nobody declared.

    ``wt/other`` is not a fleet member, so no dispatched spec's write set covers it
    and the isolation scan must report it - the write-set net, judged from content
    rather than from what a hook happened to observe.

    Each node writes its OWN id as the file's content, so every branch really does
    stray. Writing one fixed string instead would leave the second branch with nothing
    to find: the scan compares content hashes, so rewriting a file that already holds
    the identical bytes is not a change, and the second branch would pass - correctly,
    but without exercising anything.
    """

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Write the stray file, then commit as :class:`CommittingExecutor` does."""
        stray = request.isolation_root / "wt" / "other" / "STRAY"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text(request.node_dir.parent.name, encoding="utf-8")
        return await super().run(request)


class RecordingNotifier:
    """Keeps every :class:`~agentdag.application.kernel.notify.RunEvent` it is handed.

    The whole substitution is the SINK, not the port: a test asserts on the events the
    kernel emitted, which is the thing under test, rather than on a mail server's
    behaviour, which is not.
    """

    def __init__(self) -> None:
        """Start with no events recorded."""
        self.events: list[RunEvent] = []

    def emit(self, event: RunEvent) -> None:
        """Record ``event`` in arrival order."""
        self.events.append(event)


def fleet(tmp_path: Path, names: list[str]) -> tuple[GraphAArgs, list[Path]]:
    """Build ``names`` as real repositories, mirror them into a scratch tree, write the args.

    Takes no ``parallel``: how many map branches may be in flight is the
    COORDINATOR's own scheduling knob (``run_coordinator(parallel=...)``), not part of
    ``GraphAArgs`` - a caller that cares passes it straight to :func:`launch` (or its
    own ``run_coordinator`` call), never through the fleet's args.

    Args:
        tmp_path: The test's temporary directory; holds the real repositories, the
            scratch tree, the repos list and the brief.
        names: One fleet member name per repository.

    Returns:
        The run's arguments, and the bare scratch mirrors in ``names`` order.
    """
    scratch = tmp_path / "scratch"
    (scratch / "origin").mkdir(parents=True)
    origins: list[Path] = []
    for name in names:
        real = make_repo(tmp_path, name)
        origin = scratch / "origin" / f"{name}.git"
        GitCli().mirror(real, origin)
        origins.append(origin)
    (tmp_path / "REPOS.txt").write_text("".join(f"{origin}\n" for origin in origins), encoding="utf-8")
    (tmp_path / "BRIEF.md").write_text("add a line", encoding="utf-8")
    args = GraphAArgs(repos_file=tmp_path / "REPOS.txt", brief_file=tmp_path / "BRIEF.md", scratch=scratch)
    return args, origins


def policy_path() -> Path:
    """Return the shipped tier policy table's path."""
    return Path(__file__).parents[1] / "src" / "agentdag" / "policy" / "tier-policy.yaml"


def decide(
    run_dir: FsRunDir,
    verdict: str,
    *,
    by: str = "tester",
    token_id: str = "local",  # noqa: S107 - a token IDENTITY, not a secret
) -> str:
    """Answer the exact payload the suspended run is waiting on, and return its hash.

    A decision is recorded per (node id, payload hash), so a test cannot just name the
    node: it has to answer the payload the run actually presented. This reads that pair
    off ``state.json``'s suspend cursor, which is what the CLI does before showing a
    human ``nodes/<cursor>/<hash8>/payload.json``.

    Args:
        run_dir: The suspended run.
        verdict: The option id to record, e.g. ``"approve"`` or ``"hold"``.
        by: Who decided; any token id other than ``"system"`` counts as a human.
        token_id: The credential the decision was authorised with.

    Returns:
        The payload hash the decision was bound to.
    """
    state = run_dir.read_state()
    assert state.cursor is not None, "the run is not suspended: state.json has no cursor"
    assert state.cursor_payload_hash is not None, "the suspend cursor names no payload"
    run_dir.write_decision(
        Decision(
            node_id=state.cursor,
            decision=verdict,
            by=by,
            token_id=token_id,
            payload_hash=state.cursor_payload_hash,
        )
    )
    return state.cursor_payload_hash


def launch(
    tmp_path: Path,
    executor: CommittingExecutor,
    *,
    run_id: str = "r1",
    resume: str | None = None,
    parallel: int = 2,
    names: list[str] | None = None,
    git_port: GitCli | None = None,
    notifier: Notifier | None = None,
) -> tuple[RunOutcome, FsRunDir]:
    """Start (or resume) one graph A run over a real run directory and return its outcome.

    Args:
        tmp_path: The test's temporary directory.
        executor: What the ``claude`` executor row resolves to.
        run_id: The run's id, and its directory name under ``runs/``.
        resume: The resume reason, or ``None`` to start a fresh run - a fresh run
            builds the fleet, a resume reads its arguments back from the run state.
        parallel: How many map branches this launch may have in flight - the
            COORDINATOR's own knob (``run_coordinator(parallel=...)``), used AS GIVEN
            for both a fresh run and a resume alike, never read off ``GraphAArgs`` (it
            carries no such field). A caller whose test depends on the SAME value
            across a crash-and-resume pair (parallel=1, so a crash window is exactly
            one key - see the two crash tests below) must pass it explicitly on BOTH
            calls; nothing persists it in between.
        names: The fleet a FRESH run migrates; defaults to two members. An empty list
            is a fleet of none, which is what makes ``g_discover`` halt the run.
        git_port: The git port this launch runs over; defaults to the shipped adapter.
            Injected at the seam production uses, so a test can watch or interrupt the
            one effect that leaves the process without patching anything.
        notifier: Where this launch's run events go; defaults to the shipped no-op sink,
            which is also what production wires when the operator configured none - so a
            test that says nothing about notification runs the same way an unconfigured
            operator's run does.

    Returns:
        The run's outcome, and the run directory it ran over.
    """
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.open(base, run_id) if resume else FsRunDir.create(base, run_id)
    args = (
        GraphAArgs.model_validate(run_dir.read_state().args)
        if resume
        else fleet(tmp_path, ["a", "b"] if names is None else names)[0]
    )
    outcome = asyncio.run(
        run_coordinator(
            run_dir=run_dir,
            journal=JsonlJournal(run_dir.journal_path, run_dir.audit_path),
            clock=UtcClock(),
            lock=FileRunLock(),
            holder=current_holder(),
            workflow=get_workflow("graph-a"),
            args=args,
            executors={"claude": executor},
            gate_port=MakeTestGate(command=(sys.executable, "-c", "raise SystemExit(0)")),
            git=git_port if git_port is not None else GitCli(),
            scanner=IsolationScanner(),
            policy=load_policy(policy_path()),
            registry=build_op_registry(),
            sandbox=NoSandbox(),
            parallel=parallel,
            by="tester",
            token_id="local",
            resume_reason=resume,
            notifier=notifier if notifier is not None else NoNotifier(),
        )
    )
    return outcome, run_dir


class OneRowPolicy:
    """A one-row tier policy: every spec resolves to the sonnet row on the claude executor.

    ``tokens_per_row`` is set far above anything these tests charge or cap - it is the
    ceiling the run-level budget check (:meth:`~agentdag.application.kernel.context.Coordinator._run_cap_refusal`)
    reads, and nothing here is exercising THAT (see :class:`LowCeilingPolicy` for a
    policy that is). Kept generous rather than removed so a test added later that adds
    a couple more work dispatches over this same policy does not spuriously trip it.
    """

    version: str = "sha256:test"
    max_turns: int = 5
    default_node_tokens: int | None = None
    """No default cap: these doubles pin what a node's OWN declared budget does,
    and a default would silently cap every spec that declares none."""
    max_attempts: int = 1
    max_continuations: int = 3
    deny_bash: tuple[str, ...] = ("git push",)
    on_auth_failure: FailureAction = FailureAction.FAIL_RUN
    on_rate_limit: FailureAction = FailureAction.SUSPEND_RUN
    run_limits: RunLimits = RunLimits(
        tokens_per_row={"sonnet": 1_000_000_000},
        deadline_ceiling_s=999_999.0,
        per_kind_ceiling={},
        planner_kinds=[],
        top_role_budget_floor=0.0,
        max_replans=3,
        max_nodes_per_run=1000,
        max_nodes_per_plan=1000,
        max_plan_depth=5,
    )
    """Generous everywhere - :class:`LowCeilingPolicy` and :class:`LowDeadlineCeilingPolicy`
    are the ones that exercise the run-level cap and the deadline clamp; nothing here should
    trip either by accident."""

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        return ResolvedRow(alias="sonnet", executor="claude", handover_at_tokens=100_000)


class RetryingPolicy(OneRowPolicy):
    """:class:`OneRowPolicy`, but a run that gives a transiently-failing node three tries."""

    max_attempts: int = 3


class LowCeilingPolicy(OneRowPolicy):
    """:class:`OneRowPolicy`, but a run ceiling low enough for the budget-cap tests to hit."""

    run_limits: RunLimits = OneRowPolicy.run_limits.model_copy(update={"tokens_per_row": {"sonnet": 100}})


class LowDeadlineCeilingPolicy(OneRowPolicy):
    """:class:`OneRowPolicy`, but a deadline ceiling below ``work_spec``'s own ``deadline_s``.

    ``work_spec()`` declares ``deadline_s=3600``; this ceiling is well under that, so a
    dispatch under this policy proves :meth:`~agentdag.application.kernel.context.Coordinator.work`
    clamps the SPEC's requested deadline to ``policy.deadline_ceiling_s`` before it ever
    reaches :class:`~agentdag.application.kernel.ports.ExecutorRequest`.
    """

    run_limits: RunLimits = OneRowPolicy.run_limits.model_copy(update={"deadline_ceiling_s": 30.0})


class RecordingExecutor:
    """An executor that records every request it is handed and returns one fixed outcome."""

    def __init__(self, outcome: NodeOutcome) -> None:
        self.outcome = outcome
        self.requests: list[ExecutorRequest] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Record ``request`` and return the fixed outcome."""
        self.requests.append(request)
        return self.outcome


class FakeScanner:
    """An isolation scanner that records which root it was asked about."""

    def __init__(self) -> None:
        self.roots: list[Path] = []

    def snapshot(self, root: Path) -> Mapping[str, str]:
        """Record ``root`` and return a fixed manifest."""
        self.roots.append(root)
        return {"wt/a/f.py": "sha256:0"}


def outcome(charged: dict[str, int]) -> NodeOutcome:
    """Build the DONE outcome the fake executor returns."""
    return NodeOutcome(
        status=NodeStatus.DONE,
        artefact_refs=["wt/a"],
        key_facts={"commit": "a" * 40},
        typed_fields=["commit"],
        charged_tokens=charged,
        executor_used="claude",
        model_used="sonnet",
        effort_used="-",
    )


def work_spec() -> NodeSpec:
    """Build the work node spec these tests dispatch."""
    return NodeSpec(
        node_id="w_migrate@1",
        kind=Kind.WORK,
        tier_role=TierRole.STANDARD,
        isolation=Isolation.WORKTREE,
        write_set=["wt/a/**"],
        deadline_s=3600,
        budget=Budget(tokens={"sonnet": 400_000}),
    )


def fresh_run_dir(tmp_path: Path) -> FsRunDir:
    """Lay out a fresh run directory with a worktree for the node to run in."""
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "r1")
    run_dir.worktree("a").mkdir(parents=True)
    return run_dir


def wire(
    run_dir: FsRunDir,
    executor: Executor,
    scanner: FakeScanner,
    *,
    executors: Mapping[str, Executor] | None = None,
    policy: OneRowPolicy | None = None,
    gate_port: GatePort | None = None,
) -> Coordinator:
    """Build a coordinator over ``run_dir``, as a relaunch would over an existing one.

    ``executors`` defaults to ``{"claude": executor}`` (what ``OneRowPolicy`` resolves
    to); a test that must exercise a misconfigured coordinator passes its own, e.g. an
    empty mapping to prove the resolved executor is not wired. ``policy`` defaults to
    ``OneRowPolicy()``; a budget-cap test passes ``LowCeilingPolicy()`` instead.
    """
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    return Coordinator(
        run_id="r1",
        workflow="t",
        args={},
        dispatcher=Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock()),
        run_dir=run_dir,
        clock=UtcClock(),
        executors={"claude": executor} if executors is None else executors,
        gate_port=MakeTestGate() if gate_port is None else gate_port,
        git=GitCli(),
        scanner=scanner,
        policy=OneRowPolicy() if policy is None else policy,
        registry=build_op_registry(),
        sandbox=NoSandbox(),
        parallel=2,
    )


class PlanWritingExecutor:
    """An executor that writes ``plan.json`` into the node dir, as a planner node does.

    A real double rather than a patch of the read: the seam under test is "a node ran and
    left a file behind in its own dispatch directory", so the executor is where the file
    has to come from.
    """

    def __init__(self, raw: str | None) -> None:
        self.raw = raw
        self.requests: list[ExecutorRequest] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Write the plan (when this double has one) and return a DONE outcome."""
        self.requests.append(request)
        if self.raw is not None:
            (request.node_dir / PLAN_FILENAME).write_text(self.raw, encoding="utf-8")
        return outcome({"sonnet": 10})
