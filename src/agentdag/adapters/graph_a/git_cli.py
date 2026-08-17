"""GitPort over the git CLI (subprocess, utf-8, errors replaced).

``encoding`` is passed explicitly on every call: with ``text=True`` alone the
decoding falls back to the machine's locale codec, which fails differently on
Windows and POSIX and can hand back ``None`` instead of raising.

The executable is resolved to an absolute path once, because Windows'
``CreateProcess`` searches the PARENT process's ``PATH`` rather than the
environment handed to the child, so a bare name can fail to resolve there.

Contents:
    * :class:`GitCli` - the port implementation.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - driving the git CLI IS this adapter; nothing here goes through a shell
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["GitCli"]

GIT_EXECUTABLE = shutil.which("git") or "git"
"""Absolute path of the git CLI, falling back to the bare name so a missing git
still fails with a plain ``FileNotFoundError`` naming the command."""


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one git command and return the completed process.

    Args:
        args: The git arguments, without the leading ``git``.
        cwd: Directory to run in, or ``None`` for the current one.
        check: Whether a non-zero exit raises.

    Returns:
        The completed process, with stdout and stderr decoded as utf-8.
    """
    # Suppressions below: a fixed executable and an argument list, never a shell string.
    return subprocess.run(  # nosec B603  # noqa: S603
        [GIT_EXECUTABLE, *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


class GitCli:
    """Every git operation graph A performs, over the git CLI."""

    def mirror(self, source: Path, dest: Path) -> None:
        """Create a bare mirror of ``source`` at ``dest``; ``source`` is only read."""
        _git("clone", "-q", "--mirror", str(source), str(dest))

    def clone(self, origin: Path, dest: Path) -> None:
        """Clone ``origin`` into a working tree at ``dest`` with a committer identity."""
        # core.fileMode=false: the shared softdev mount reports stray executable bits,
        # which would otherwise turn into mode churn in every commit the work node makes.
        _git("-c", "core.fileMode=false", "clone", "-q", str(origin), str(dest))
        _git("config", "user.email", "agentdag@localhost", cwd=dest)
        _git("config", "user.name", "agentdag", cwd=dest)

    def head_sha(self, repo: Path) -> str:
        """Return the commit id ``HEAD`` points at in ``repo``."""
        return _git("rev-parse", "--verify", "-q", "HEAD", cwd=repo).stdout.strip()

    def has_commit(self, repo: Path, sha: str) -> bool:
        """Report whether ``repo`` already contains commit ``sha``."""
        return _git("cat-file", "-e", f"{sha}^{{commit}}", cwd=repo, check=False).returncode == 0

    def default_branch(self, bare_repo: Path) -> str:
        """Return the branch ``HEAD`` points at in a bare repository."""
        return _git("symbolic-ref", "--short", "HEAD", cwd=bare_repo).stdout.strip()

    def push(self, worktree: Path, target: Path, branch: str) -> None:
        """Push ``worktree``'s ``HEAD`` to ``branch`` of ``target``."""
        _git("push", "-q", str(target), f"HEAD:{branch}", cwd=worktree)
