"""CLI stories for ``graph-a``: build a scratch fleet, run the graph, refuse a real target.

The commands are driven through the real root group with the real Click runner, exactly
as ``test_cli_config.py`` does, and the graph A wiring reaches them through the same
``AppServices`` injection production uses. Only the work node and the approval are
substituted: git, the gate and the run store are the real adapters over real temporary
repositories and real child processes.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.graph_a.store_fs import FsRunStore
from agentdag.application.graph_a_ports import GraphAWiring, WorkPort
from agentdag.composition import AppServices, build_production
from agentdag.domain.graph_a import WorkResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner, Result

GREEN = "test:\n\t@exit 0\n"
GIT = shutil.which("git") or "git"


def git(*args: str, cwd: Path) -> str:
    """Drive git independently of the code under test, so assertions stay honest."""
    return subprocess.run(  # nosec B603  # noqa: S603
        [GIT, *args], cwd=cwd, check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout.strip()


def make_repo(root: Path, name: str) -> Path:
    """Create a real git repository with a green ``make test`` target."""
    repo = root / name
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@example.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "Makefile").write_text(GREEN)
    (repo / "README.md").write_text(f"# {name}\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    return repo


class CommittingWork:
    """Stands in for the Claude node: edits a file and commits, like the brief asks."""

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        (worktree / "CHANGELOG.md").write_text(brief)
        git("add", "-A", cwd=worktree)
        git("commit", "-q", "-m", "baseline change", cwd=worktree)
        return WorkResult(ok=True, num_turns=1, input_tokens=10, output_tokens=5, cost_usd=0.0)


class RecordingWork:
    """Writes down every dispatch, so a test can assert the graph never got that far."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        self.calls.append(worktree)
        return WorkResult(ok=True, num_turns=1, input_tokens=10, output_tokens=5, cost_usd=0.0)


class YesApprover:
    """Stands in for the operator, who said yes."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def confirm(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return True


def services_wiring(store: FsRunStore, approve: YesApprover, work: WorkPort | None = None) -> Callable[[], AppServices]:
    """Return a services factory whose ``wire_graph_a`` hands back these fakes."""
    wiring = GraphAWiring(
        git=GitCli(),
        gate=MakeTestGate(command=(sys.executable, "-c", "raise SystemExit(0)")),
        work=work or CommittingWork(),
        approve=approve,
        store=store,
    )

    def _wire(*, runs: Path) -> GraphAWiring:
        return wiring

    prod = build_production()
    services = AppServices(
        get_config=prod.get_config,
        get_default_config_path=prod.get_default_config_path,
        deploy_configuration=prod.deploy_configuration,
        display_config=prod.display_config,
        send_email=prod.send_email,
        send_notification=prod.send_notification,
        load_email_config_from_dict=prod.load_email_config_from_dict,
        init_logging=prod.init_logging,
        wire_graph_a=_wire,
        wire_kernel=prod.wire_kernel,
    )
    return lambda: services


def write_lines(path: Path, lines: list[str]) -> Path:
    """Write one path per line, the shape ``graph-a`` reads."""
    path.write_text("".join(f"{line}\n" for line in lines))
    return path


@pytest.mark.os_agnostic
def test_when_graph_a_scratch_runs_it_mirrors_and_prints_the_repos_file(
    cli_runner: CliRunner,
    tmp_path: Path,
    production_factory: Callable[[], AppServices],
) -> None:
    """graph-a scratch mirrors each real repository and names the list it wrote."""
    real = make_repo(tmp_path / "real", "one")
    listing = write_lines(tmp_path / "real-repos.txt", [str(real)])
    scratch = tmp_path / "scratch"

    result: Result = cli_runner.invoke(
        cli_mod.cli, ["graph-a", "scratch", str(listing), "--scratch", str(scratch)], obj=production_factory
    )

    assert result.exit_code == 0
    assert str(scratch / "REPOS.txt") in result.output
    origin = scratch / "origin" / "one.git"
    assert (origin / "HEAD").is_file()  # a bare clone, not a working tree
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "main", cwd=real)
    assert (scratch / "REPOS.txt").read_text() == f"{origin}\n"


@pytest.mark.os_agnostic
def test_when_graph_a_run_is_approved_it_pushes_the_scratch_origin(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """graph-a run maps over the fleet and pushes what passed, once approved."""
    real = make_repo(tmp_path / "real", "two")
    scratch = tmp_path / "scratch"
    origin = scratch / "origin" / "two.git"
    origin.parent.mkdir(parents=True)
    GitCli().mirror(real, origin)
    before = git("rev-parse", "main", cwd=origin)
    repos = write_lines(tmp_path / "repos.txt", [str(origin)])
    brief = tmp_path / "brief.md"
    brief.write_text("add a line\n")
    store = FsRunStore.create(tmp_path / "runs")
    approver = YesApprover()

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["graph-a", "run", str(repos), str(brief), "--scratch", str(scratch), "--parallel", "1"],
        obj=services_wiring(store, approver),
    )

    assert result.exit_code == 0
    assert len(approver.prompts) == 1
    assert git("rev-parse", "main", cwd=origin) != before
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "HEAD", cwd=store.worktree("two"))
    assert str(store.root / "tally.json") in result.output


@pytest.mark.os_agnostic
def test_when_graph_a_run_targets_a_non_scratch_repo_it_exits_invalid_argument(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """A push target outside the scratch tree is a hard stop, not a skipped repository.

    It stops the run BEFORE anything is dispatched: the whole fleet's model spend and an
    approval prompt for pushes that cannot happen would otherwise come first.
    """
    real = make_repo(tmp_path / "real", "three")
    before = git("rev-parse", "main", cwd=real)
    repos = write_lines(tmp_path / "repos.txt", [str(real)])
    brief = tmp_path / "brief.md"
    brief.write_text("add a line\n")
    store = FsRunStore.create(tmp_path / "runs")
    work = RecordingWork()
    approver = YesApprover()

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["graph-a", "run", str(repos), str(brief), "--scratch", str(tmp_path / "scratch"), "--parallel", "1"],
        obj=services_wiring(store, approver, work),
    )

    assert result.exit_code == ExitCode.INVALID_ARGUMENT
    assert "is not under" in result.output
    assert git("rev-parse", "main", cwd=real) == before
    assert work.calls == []  # refused before the work node ran
    assert approver.prompts == []  # and before anybody was asked to approve
    assert not (store.root / "tally.json").exists()


@pytest.mark.os_agnostic
def test_when_graph_a_run_gets_parallel_zero_it_refuses_instead_of_hanging(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """``--parallel 0`` would build a Semaphore nobody can enter, so Click refuses it."""
    repos = write_lines(tmp_path / "repos.txt", [str(tmp_path / "nothing.git")])
    brief = tmp_path / "brief.md"
    brief.write_text("add a line\n")
    store = FsRunStore.create(tmp_path / "runs")

    result: Result = cli_runner.invoke(
        cli_mod.cli,
        ["graph-a", "run", str(repos), str(brief), "--parallel", "0"],
        obj=services_wiring(store, YesApprover()),
    )

    assert result.exit_code == 2  # Click's UsageError: refused while parsing, before the callback
    assert not (store.root / "tally.json").exists()  # the graph never started
    assert "--parallel" in result.output
