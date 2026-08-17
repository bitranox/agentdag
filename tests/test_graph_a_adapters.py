"""Adapter tests for graph A, over real temporary git repositories and real subprocesses.

Nothing is patched here: the git adapter drives the real git CLI, the gate adapter
runs a real child process and reports its real exit code, and the store makes real
directories. Only the model call is out of reach, and it has no adapter test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.graph_a.store_fs import FsRunStore

if TYPE_CHECKING:
    from pathlib import Path

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


def test_git_cli_mirror_clone_head_and_default_branch(tmp_path: Path) -> None:
    g = GitCli()
    real = make_repo(tmp_path, "r", "test:\n\t@exit 0\n")
    bare = tmp_path / "r.git"
    g.mirror(real, bare)
    wt = tmp_path / "wt"
    g.clone(bare, wt)
    assert g.head_sha(wt) == git("rev-parse", "HEAD", cwd=real)
    assert g.default_branch(bare) == "main"
    assert g.has_commit(bare, g.head_sha(wt))
    assert not g.has_commit(bare, "0" * 40)


def test_git_cli_push_moves_the_bare_target_and_leaves_the_source_alone(tmp_path: Path) -> None:
    g = GitCli()
    real = make_repo(tmp_path, "src", "test:\n\t@exit 0\n")
    before = git("rev-parse", "main", cwd=real)
    bare = tmp_path / "src.git"
    g.mirror(real, bare)
    wt = tmp_path / "wt"
    g.clone(bare, wt)
    (wt / "NEW.md").write_text("new\n")
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m", "add", cwd=wt)
    g.push(wt, bare, g.default_branch(bare))
    assert git("rev-parse", "main", cwd=bare) == g.head_sha(wt)
    assert git("rev-parse", "main", cwd=real) == before


def test_gate_returns_the_command_exit_code_under_the_lock(tmp_path: Path) -> None:
    for code in (0, 1, 3):
        gate = MakeTestGate(lock=tmp_path / "l", command=(sys.executable, "-c", f"raise SystemExit({code})"))
        assert gate.run(tmp_path, tmp_path / f"g{code}.log") == code


def test_gate_writes_the_child_output_to_the_log(tmp_path: Path) -> None:
    program = "import sys; print('on stdout'); print('on stderr', file=sys.stderr)"
    gate = MakeTestGate(lock=tmp_path / "l", command=(sys.executable, "-c", program))
    log = tmp_path / "logs" / "out.log"
    assert gate.run(tmp_path, log) == 0
    written = log.read_text()
    assert "on stdout" in written
    assert "on stderr" in written


@pytest.mark.integration
def test_gate_runs_real_make_test(tmp_path: Path) -> None:
    # The exact non-zero code is a make implementation detail (GNU make answers a failing
    # recipe with 2, not the recipe's own 1), so the contract asserted here is that a red
    # gate is distinguishable from a green one, with the green repo as the control.
    green = make_repo(tmp_path, "green", "test:\n\t@exit 0\n")
    red = make_repo(tmp_path, "red", "test:\n\t@exit 1\n")
    gate = MakeTestGate(lock=tmp_path / "l")
    assert gate.run(green, tmp_path / "green.log") == 0
    assert gate.run(red, tmp_path / "red.log") != 0


def test_store_layout(tmp_path: Path) -> None:
    s = FsRunStore.create(tmp_path / "runs")
    assert all((s.root / d).is_dir() for d in ("wt", "tally", "intents", "done", "log", "home"))
    s.write_json("tally/x.json", "{}")
    assert (s.root / "tally/x.json").read_text() == "{}"
    assert s.marker("k") == s.root / "done" / "k"


def test_store_home_is_created_per_node(tmp_path: Path) -> None:
    s = FsRunStore.create(tmp_path / "runs")
    home = s.home("one")
    assert home.is_dir()
    assert home != s.home("two")
