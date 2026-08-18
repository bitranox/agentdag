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
    * :func:`fleet` - mirror N repositories into a scratch tree and build the run's args.
    * :func:`policy_path` - the shipped tier policy table.
    * :func:`launch` - start or resume one graph A run over a real run directory.
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
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.workflows import get_workflow
from agentdag.application.workflows.graph_a import GraphAArgs
from agentdag.domain.models import NodeOutcome, NodeStatus, Tokens

if TYPE_CHECKING:
    from agentdag.application.kernel.ports import ExecutorRequest
    from agentdag.application.kernel.run import RunOutcome

__all__ = [
    "CommittingExecutor",
    "StrayExecutor",
    "fleet",
    "git",
    "launch",
    "make_repo",
    "policy_path",
]


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
        calls: The node id of every dispatch this executor was handed, in order.
    """

    def __init__(self, crash_on: str | None = None, crash_after: str | None = None) -> None:
        """Bind the optional crash nodes; ``calls`` starts empty."""
        self.crash_on = crash_on
        self.crash_after = crash_after
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


def fleet(tmp_path: Path, names: list[str], *, parallel: int) -> tuple[GraphAArgs, list[Path]]:
    """Build ``names`` as real repositories, mirror them into a scratch tree, write the args.

    Args:
        tmp_path: The test's temporary directory; holds the real repositories, the
            scratch tree, the repos list and the brief.
        names: One fleet member name per repository.
        parallel: How many map branches the run may have in flight.

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
    args = GraphAArgs(
        repos_file=tmp_path / "REPOS.txt",
        brief_file=tmp_path / "BRIEF.md",
        scratch=scratch,
        parallel=parallel,
    )
    return args, origins


def policy_path() -> Path:
    """Return the shipped tier policy table's path."""
    return Path(__file__).parents[1] / "src" / "agentdag" / "policy" / "tier-policy.yaml"


def launch(
    tmp_path: Path,
    executor: CommittingExecutor,
    *,
    run_id: str = "r1",
    resume: str | None = None,
    parallel: int = 2,
    names: list[str] | None = None,
) -> tuple[RunOutcome, FsRunDir]:
    """Start (or resume) one graph A run over a real run directory and return its outcome.

    Args:
        tmp_path: The test's temporary directory.
        executor: What the ``claude`` executor row resolves to.
        run_id: The run's id, and its directory name under ``runs/``.
        resume: The resume reason, or ``None`` to start a fresh run - a fresh run
            builds the fleet, a resume reads its arguments back from the run state.
        parallel: How many map branches a FRESH run may have in flight; a resume
            takes the value its state already carries.
        names: The fleet a FRESH run migrates; defaults to two members. An empty list
            is a fleet of none, which is what makes ``g_discover`` halt the run.

    Returns:
        The run's outcome, and the run directory it ran over.
    """
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.open(base, run_id) if resume else FsRunDir.create(base, run_id)
    args = (
        GraphAArgs.model_validate(run_dir.read_state().args)
        if resume
        else fleet(tmp_path, ["a", "b"] if names is None else names, parallel=parallel)[0]
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
            gate_port=MakeTestGate(lock=tmp_path / "gate.lock", command=(sys.executable, "-c", "raise SystemExit(0)")),
            git=GitCli(),
            scanner=IsolationScanner(),
            policy=load_policy(policy_path()),
            parallel=args.parallel,
            by="tester",
            token_id="local",
            resume_reason=resume,
        )
    )
    return outcome, run_dir
