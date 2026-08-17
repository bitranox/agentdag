"""Adapter tests for graph A, over real temporary git repositories and real subprocesses.

Nothing is patched here: the git adapter drives the real git CLI, the gate adapter
runs a real child process and reports its real exit code, and the store makes real
directories. The work adapter's MODEL call is out of reach and has no adapter test, but
its credential handling does: the source path is a constructor argument, so the copy can
be exercised against a temporary file instead of the operator's own login.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.graph_a.store_fs import FsRunStore
from agentdag.adapters.graph_a.work_claude_sdk import ClaudeSdkWork

if TYPE_CHECKING:
    from pathlib import Path

GIT = shutil.which("git") or "git"
CREDENTIALS_REL = ".claude/.credentials.json"


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
    assert g.ref_sha(bare, "main") == g.head_sha(wt)
    assert g.ref_sha(bare, "no-such-branch") is None


def test_git_cli_ref_sha_reads_the_ref_not_the_object_store(tmp_path: Path) -> None:
    """A commit present as an OBJECT but not on the branch must not read as applied.

    This is the failure an object-existence check cannot see: a push whose objects
    transferred and whose ref update was then rejected.
    """
    g = GitCli()
    real = make_repo(tmp_path, "refs", "test:\n\t@exit 0\n")
    bare = tmp_path / "refs.git"
    g.mirror(real, bare)
    before = git("rev-parse", "main", cwd=bare)
    wt = tmp_path / "wt"
    g.clone(bare, wt)
    (wt / "NEW.md").write_text("new\n")
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m", "unpushed", cwd=wt)
    ahead = g.head_sha(wt)
    # transfer the objects WITHOUT moving the branch, exactly as a rejected update leaves it
    git("push", "-q", str(bare), f"{ahead}:refs/tmp/objects-only", cwd=wt)
    assert git("cat-file", "-e", f"{ahead}^{{commit}}", cwd=bare) == ""  # the object IS there
    assert g.ref_sha(bare, "main") == before
    assert g.ref_sha(bare, "main") != ahead


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


def credential_source(tmp_path: Path, text: str = '{"token": "operator"}') -> Path:
    """Write a stand-in for the operator's credential and return its path."""
    source = tmp_path / "operator" / ".claude" / ".credentials.json"
    source.parent.mkdir(parents=True)
    source.write_text(text)
    return source


def test_work_gives_each_node_its_own_credential_copy(tmp_path: Path) -> None:
    source = credential_source(tmp_path)
    work = ClaudeSdkWork(credentials_source=source)

    first = work.prepare_config_dir(tmp_path / "home-one")
    second = work.prepare_config_dir(tmp_path / "home-two")

    assert first != second
    for config_dir in (first, second):
        copy = config_dir / ".credentials.json"
        assert copy.is_file()
        assert not copy.is_symlink()  # a link would hand the node the operator's own file
        assert copy.read_text() == source.read_text()
        assert copy.resolve() != source.resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits; Windows has no 0600 equivalent")
def test_work_creates_the_credential_copy_owner_only(tmp_path: Path) -> None:
    work = ClaudeSdkWork(credentials_source=credential_source(tmp_path))
    copy = work.prepare_config_dir(tmp_path / "home") / ".credentials.json"
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600


def test_work_leaves_an_existing_credential_copy_alone(tmp_path: Path) -> None:
    """A node may have refreshed its token into its own copy; do not clobber it."""
    work = ClaudeSdkWork(credentials_source=credential_source(tmp_path))
    home = tmp_path / "home"
    config_dir = home / ".claude"
    config_dir.mkdir(parents=True)
    (config_dir / ".credentials.json").write_text('{"token": "refreshed-by-the-node"}')

    work.prepare_config_dir(home)

    assert (config_dir / ".credentials.json").read_text() == '{"token": "refreshed-by-the-node"}'


def test_work_with_no_source_credential_writes_nothing(tmp_path: Path) -> None:
    """An operator with no credential file is not an error: the CLI reports it."""
    work = ClaudeSdkWork(credentials_source=tmp_path / "nowhere" / ".credentials.json")

    config_dir = work.prepare_config_dir(tmp_path / "home")

    assert not (config_dir / ".credentials.json").exists()


def test_work_never_writes_to_the_source_credential(tmp_path: Path) -> None:
    """A read-only source must still work: the copy is the only thing ever written.

    The control is the mode itself - if the adapter opened the source for writing, or
    wrote through a link into it, this raises PermissionError instead of passing.
    """
    source = credential_source(tmp_path, '{"token": "read-only-operator"}')
    source.chmod(0o400)
    before = source.stat().st_mtime_ns
    work = ClaudeSdkWork(credentials_source=source)

    copy = work.prepare_config_dir(tmp_path / "home") / ".credentials.json"
    os.utime(copy, (0, 0))  # a link or a shared inode would carry this back to the source

    assert copy.read_text() == '{"token": "read-only-operator"}'
    assert source.read_text() == '{"token": "read-only-operator"}'
    assert source.stat().st_mtime_ns == before
