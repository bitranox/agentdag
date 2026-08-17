"""End-to-end tests for the graph A program over real temporary git repositories.

The work port is the ONE external edge (a model call), so it is the only thing
substituted here; git, the gate and the run store are the real adapters. The fake
work node commits a file, which is exactly what a good agent does.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.graph_a.store_fs import FsRunStore
from agentdag.application.graph_a import apply, make_scratch_fleet, run_graph
from agentdag.domain.graph_a import PushIntent, WorkResult

GREEN = "test:\n\t@exit 0\n"
GIT = shutil.which("git") or "git"


def git(*args: str, cwd: Path) -> str:
    """Drive git independently of the adapter under test, so assertions stay honest."""
    return subprocess.run(  # nosec B603  # noqa: S603
        [GIT, *args], cwd=cwd, check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout.strip()


def make_repo(root: Path, name: str, makefile: str) -> Path:
    repo = root / name
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@example.invalid", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "Makefile").write_text(makefile)
    (repo / "README.md").write_text(f"# {name}\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    return repo


class CommittingWork:
    """Stands in for the Claude node: edits a file and commits, like the brief asks."""

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        (worktree / "CHANGELOG.md").write_text(brief + "\n")
        git("add", "-A", cwd=worktree)
        git("commit", "-q", "-m", "baseline change", cwd=worktree)
        return WorkResult(ok=True, num_turns=1, input_tokens=10, output_tokens=5, cost_usd=0.0)


class FailingWork:
    """Stands in for a work node that could not do the job."""

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        return WorkResult(ok=False, error="refused")


class RecordingWork:
    """Writes down every dispatch, so a test can assert the graph never got that far."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        self.calls.append(worktree)
        return WorkResult(ok=True, num_turns=1, input_tokens=10, output_tokens=5, cost_usd=0.0)


class RecordingGit:
    """The real git adapter with every push it was asked to make written down.

    Delegation, not a patch: ``apply`` takes the git port as a parameter, so this is
    injected at the same seam production uses and every call still hits real git.
    """

    def __init__(self) -> None:
        self._git = GitCli()
        self.pushes: list[tuple[Path, str]] = []

    def mirror(self, source: Path, dest: Path) -> None:
        self._git.mirror(source, dest)

    def clone(self, origin: Path, dest: Path) -> None:
        self._git.clone(origin, dest)

    def head_sha(self, repo: Path) -> str:
        return self._git.head_sha(repo)

    def ref_sha(self, repo: Path, ref: str) -> str | None:
        return self._git.ref_sha(repo, ref)

    def default_branch(self, bare_repo: Path) -> str:
        return self._git.default_branch(bare_repo)

    def push(self, worktree: Path, target: Path, branch: str) -> None:
        self.pushes.append((target, branch))
        self._git.push(worktree, target, branch)


class YesApprover:
    def confirm(self, prompt: str) -> bool:
        return True


class NoApprover:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def confirm(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return False


def true_gate(tmp_path: Path) -> MakeTestGate:
    # the gate is a subprocess exit code; use the interpreter so the unit test needs no `make`
    return MakeTestGate(lock=tmp_path / "gate.lock", command=(sys.executable, "-c", "raise SystemExit(0)"))


def false_gate(tmp_path: Path) -> MakeTestGate:
    return MakeTestGate(lock=tmp_path / "gate.lock", command=(sys.executable, "-c", "raise SystemExit(1)"))


def scratch_fleet(tmp_path: Path, git_port: GitCli, names: list[str]) -> tuple[Path, list[Path]]:
    """Build a scratch origin per named real repository and return the scratch root."""
    scratch = tmp_path / "scratch"
    (scratch / "origin").mkdir(parents=True)
    origins: list[Path] = []
    for name in names:
        real = make_repo(tmp_path, name, GREEN)
        origin = scratch / "origin" / f"{name}.git"
        git_port.mirror(real, origin)
        origins.append(origin)
    return scratch, origins


def test_run_graph_end_to_end_pushes_after_approve(tmp_path: Path) -> None:
    gitp = GitCli()
    real = make_repo(tmp_path, "real1", GREEN)
    real_before = git("rev-parse", "main", cwd=real)
    scratch = tmp_path / "scratch"
    origin = scratch / "origin" / "real1.git"
    origin.parent.mkdir(parents=True)
    gitp.mirror(real, origin)
    store = FsRunStore.create(tmp_path / "runs")
    rc = asyncio.run(
        run_graph(
            origins=[origin],
            brief="add a line",
            model="sonnet",
            parallel=2,
            scratch_root=scratch,
            git=gitp,
            gate=true_gate(tmp_path),
            work=CommittingWork(),
            approve=YesApprover(),
            store=store,
        )
    )
    assert rc == 0
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "HEAD", cwd=store.worktree("real1"))
    # the REAL repo is untouched: it still points where it did BEFORE the run, which a
    # bare "differs from the origin" would also say if the run had rewritten it.
    assert git("rev-parse", "main", cwd=real) == real_before
    assert (store.root / "tally.json").exists()


def test_run_graph_maps_over_the_whole_fleet(tmp_path: Path) -> None:
    gitp = GitCli()
    scratch, origins = scratch_fleet(tmp_path, gitp, ["one", "two", "three"])
    store = FsRunStore.create(tmp_path / "runs")
    rc = asyncio.run(
        run_graph(
            origins=origins,
            brief="add a line",
            model="sonnet",
            parallel=2,
            scratch_root=scratch,
            git=gitp,
            gate=true_gate(tmp_path),
            work=CommittingWork(),
            approve=YesApprover(),
            store=store,
        )
    )
    assert rc == 0
    for origin in origins:
        name = origin.name.removesuffix(".git")
        assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "HEAD", cwd=store.worktree(name))
        assert (store.root / "tally" / f"{name}.json").exists()


def test_run_graph_declining_the_approval_pushes_nothing(tmp_path: Path) -> None:
    gitp = GitCli()
    real = make_repo(tmp_path, "declined", GREEN)
    scratch = tmp_path / "scratch"
    origin = scratch / "origin" / "declined.git"
    origin.parent.mkdir(parents=True)
    gitp.mirror(real, origin)
    before = git("rev-parse", "main", cwd=origin)
    approver = NoApprover()
    store = FsRunStore.create(tmp_path / "runs")
    rc = asyncio.run(
        run_graph(
            origins=[origin],
            brief="add a line",
            model="sonnet",
            parallel=1,
            scratch_root=scratch,
            git=gitp,
            gate=true_gate(tmp_path),
            work=CommittingWork(),
            approve=approver,
            store=store,
        )
    )
    assert rc == 0
    assert git("rev-parse", "main", cwd=origin) == before
    assert len(approver.prompts) == 1
    assert "declined.git" in approver.prompts[0]


def test_run_graph_a_failed_gate_is_never_staged(tmp_path: Path) -> None:
    gitp = GitCli()
    real = make_repo(tmp_path, "red", GREEN)
    scratch = tmp_path / "scratch"
    origin = scratch / "origin" / "red.git"
    origin.parent.mkdir(parents=True)
    gitp.mirror(real, origin)
    before = git("rev-parse", "main", cwd=origin)
    approver = NoApprover()
    store = FsRunStore.create(tmp_path / "runs")
    rc = asyncio.run(
        run_graph(
            origins=[origin],
            brief="add a line",
            model="sonnet",
            parallel=1,
            scratch_root=scratch,
            git=gitp,
            gate=false_gate(tmp_path),
            work=CommittingWork(),
            approve=approver,
            store=store,
        )
    )
    assert rc == 0
    assert git("rev-parse", "main", cwd=origin) == before
    assert approver.prompts == []  # nothing pushable, so nobody is asked
    tally = (store.root / "tally.json").read_text()
    assert '"failed"' in tally


def test_run_graph_a_failed_work_node_never_runs_the_gate(tmp_path: Path) -> None:
    gitp = GitCli()
    real = make_repo(tmp_path, "nowork", GREEN)
    scratch = tmp_path / "scratch"
    origin = scratch / "origin" / "nowork.git"
    origin.parent.mkdir(parents=True)
    gitp.mirror(real, origin)
    store = FsRunStore.create(tmp_path / "runs")
    # a green gate: the assertion is that it never RAN, proved by the absence of its log
    unused_gate = MakeTestGate(lock=tmp_path / "l", command=(sys.executable, "-c", "raise SystemExit(0)"))
    rc = asyncio.run(
        run_graph(
            origins=[origin],
            brief="add a line",
            model="sonnet",
            parallel=1,
            scratch_root=scratch,
            git=gitp,
            gate=unused_gate,
            work=FailingWork(),
            approve=NoApprover(),
            store=store,
        )
    )
    assert rc == 0
    assert not (store.root / "log" / "nowork.test.log").exists()
    assert '"work-failed"' in (store.root / "tally" / "nowork.json").read_text()


def test_run_graph_with_no_origins_halts(tmp_path: Path) -> None:
    store = FsRunStore.create(tmp_path / "runs")
    rc = asyncio.run(
        run_graph(
            origins=[],
            brief="add a line",
            model="sonnet",
            parallel=1,
            scratch_root=tmp_path / "scratch",
            git=GitCli(),
            gate=true_gate(tmp_path),
            work=CommittingWork(),
            approve=NoApprover(),
            store=store,
        )
    )
    assert rc == 0


def test_apply_replay_pushes_nothing_and_refuses_non_scratch(tmp_path: Path) -> None:
    gitp = GitCli()
    real = make_repo(tmp_path, "p", GREEN)
    scratch = tmp_path / "s"
    origin = scratch / "origin" / "p.git"
    origin.parent.mkdir(parents=True)
    gitp.mirror(real, origin)
    store = FsRunStore.create(tmp_path / "runs")
    wt = store.worktree("p")
    gitp.clone(origin, wt)
    (wt / "x").write_text("x")
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m", "c", cwd=wt)
    sha = gitp.head_sha(wt)
    intents = [PushIntent(repo=origin, head_sha=sha, dedup_key=f"p.git-{sha}")]
    assert apply(intents, scratch_root=scratch, git=gitp, store=store) == ["pushed"]
    assert apply(intents, scratch_root=scratch, git=gitp, store=store) == ["already-done"]
    with pytest.raises(ValueError, match="not under"):
        apply(
            [
                PushIntent(
                    repo=Path("/media/srv-main-softdev/projects/public/libs/x"), head_sha="0" * 40, dedup_key="x-0"
                )
            ],
            scratch_root=scratch,
            git=gitp,
            store=store,
        )


def staged_push(tmp_path: Path, name: str) -> tuple[Path, Path, FsRunStore, PushIntent]:
    """Build a scratch origin with one unpushed commit in its worktree, ready to apply."""
    gitp = GitCli()
    real = make_repo(tmp_path, name, GREEN)
    scratch = tmp_path / "s"
    origin = scratch / "origin" / f"{name}.git"
    origin.parent.mkdir(parents=True, exist_ok=True)
    gitp.mirror(real, origin)
    store = FsRunStore.create(tmp_path / "runs")
    worktree = store.worktree(name)
    gitp.clone(origin, worktree)
    (worktree / "x").write_text("x")
    git("add", "-A", cwd=worktree)
    git("commit", "-q", "-m", "c", cwd=worktree)
    sha = gitp.head_sha(worktree)
    return scratch, origin, store, PushIntent(repo=origin, head_sha=sha, dedup_key=f"{name}.git-{sha}")


def test_apply_pushes_when_the_commit_is_present_but_the_branch_is_behind(tmp_path: Path) -> None:
    """A rejected ref update leaves the OBJECT in the target; the push must still happen."""
    scratch, origin, store, intent = staged_push(tmp_path, "objectsonly")
    worktree = store.worktree("objectsonly")
    before = git("rev-parse", "main", cwd=origin)
    # exactly what a transferred-but-rejected push leaves behind: the object, not the ref
    git("push", "-q", str(origin), f"{intent.head_sha}:refs/tmp/objects-only", cwd=worktree)
    assert git("rev-parse", "main", cwd=origin) == before
    recorder = RecordingGit()

    assert apply([intent], scratch_root=scratch, git=recorder, store=store) == ["pushed"]

    assert recorder.pushes == [(origin, "main")]
    assert git("rev-parse", "main", cwd=origin) == intent.head_sha


def test_apply_reports_already_present_without_pushing_when_the_ref_matches(tmp_path: Path) -> None:
    scratch, origin, store, intent = staged_push(tmp_path, "alreadythere")
    worktree = store.worktree("alreadythere")
    git("push", "-q", str(origin), "HEAD:main", cwd=worktree)  # somebody else already applied it
    recorder = RecordingGit()

    assert apply([intent], scratch_root=scratch, git=recorder, store=store) == ["already-present"]

    assert recorder.pushes == []
    assert store.marker(intent.dedup_key).exists()
    assert git("rev-parse", "main", cwd=origin) == intent.head_sha


def test_apply_validates_every_target_before_pushing_any(tmp_path: Path) -> None:
    scratch, origin, store, intent = staged_push(tmp_path, "validated")
    outside = PushIntent(repo=tmp_path / "elsewhere.git", head_sha="0" * 40, dedup_key="elsewhere.git-0")
    before = git("rev-parse", "main", cwd=origin)
    recorder = RecordingGit()

    with pytest.raises(ValueError, match="not under"):
        apply([intent, outside], scratch_root=scratch, git=recorder, store=store)

    assert recorder.pushes == []
    assert git("rev-parse", "main", cwd=origin) == before
    assert not store.marker(intent.dedup_key).exists()


def test_make_scratch_fleet_refuses_two_repositories_sharing_a_basename(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="share the basename") as raised:
        make_scratch_fleet([tmp_path / "a" / "foo", tmp_path / "b" / "foo"], tmp_path / "scratch", GitCli())
    message = str(raised.value)
    assert str(tmp_path / "a" / "foo") in message
    assert str(tmp_path / "b" / "foo") in message


def test_run_graph_refuses_two_origins_sharing_a_basename(tmp_path: Path) -> None:
    store = FsRunStore.create(tmp_path / "runs")
    with pytest.raises(ValueError, match="share the basename"):
        asyncio.run(
            run_graph(
                origins=[tmp_path / "one" / "foo.git", tmp_path / "two" / "foo.git"],
                brief="add a line",
                model="sonnet",
                parallel=1,
                scratch_root=tmp_path / "scratch",
                git=GitCli(),
                gate=true_gate(tmp_path),
                work=CommittingWork(),
                approve=NoApprover(),
                store=store,
            )
        )


def test_run_graph_refuses_a_non_scratch_origin_before_dispatching_any_work(tmp_path: Path) -> None:
    """The push-target guard fires before the fleet's spend, not after it.

    Checked only in ``apply``, this refusal arrives once every node has already run and
    the operator has been asked to approve pushes that cannot happen.
    """
    real = make_repo(tmp_path, "notscratch", GREEN)
    store = FsRunStore.create(tmp_path / "runs")
    work = RecordingWork()
    approver = NoApprover()

    with pytest.raises(ValueError, match="not under"):
        asyncio.run(
            run_graph(
                origins=[real],
                brief="add a line",
                model="sonnet",
                parallel=1,
                scratch_root=tmp_path / "scratch",
                git=GitCli(),
                gate=true_gate(tmp_path),
                work=work,
                approve=approver,
                store=store,
            )
        )

    assert work.calls == []  # no model spend
    assert approver.prompts == []  # nobody asked to approve an impossible push
    assert not (store.root / "tally.json").exists()


def test_make_scratch_fleet_reuses_a_mirror_and_refresh_rebuilds_it(tmp_path: Path) -> None:
    gitp = GitCli()
    real = make_repo(tmp_path, "stale", GREEN)
    scratch = tmp_path / "scratch"
    origin = make_scratch_fleet([real], scratch, gitp)[0]
    mirrored_at = git("rev-parse", "main", cwd=origin)
    (real / "LATER.md").write_text("later\n")
    git("add", "-A", cwd=real)
    git("commit", "-q", "-m", "after the mirror", cwd=real)

    assert make_scratch_fleet([real], scratch, gitp) == [origin]
    assert git("rev-parse", "main", cwd=origin) == mirrored_at  # stale, and silently so

    assert make_scratch_fleet([real], scratch, gitp, refresh=True) == [origin]
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "main", cwd=real)
