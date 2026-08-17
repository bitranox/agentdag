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
        """Create a bare mirror of ``source`` at ``dest``; ``source`` is only read.

        The mirror's ``origin`` remote is removed afterwards. A mirror clone records the
        real repository as a remote with ``remote.origin.mirror=true``, which is a live
        write route from the scratch tree back into the fleet's real repositories, and
        nothing needs it: a refresh re-reads the path from the real-repos list.
        """
        _git("clone", "-q", "--mirror", str(source), str(dest))
        _git("remote", "remove", "origin", cwd=dest)

    def clone(self, origin: Path, dest: Path) -> None:
        """Clone ``origin`` into a working tree at ``dest`` with a committer identity.

        The worktree keeps no remote: ``apply`` pushes by absolute path, so the clone's
        ``origin`` is used by nobody and a work node's reflex ``git push`` then has
        nowhere to go. This is not containment - a node with unrestricted Bash can still
        push to any path it can name, and that is a sandbox, not a config edit. It
        removes the route a node reaches for without thinking.
        """
        # core.fileMode=false: the shared softdev mount reports stray executable bits,
        # which would otherwise turn into mode churn in every commit the work node makes.
        _git("-c", "core.fileMode=false", "clone", "-q", str(origin), str(dest))
        _git("remote", "remove", "origin", cwd=dest)
        _git("config", "user.email", "agentdag@localhost", cwd=dest)
        _git("config", "user.name", "agentdag", cwd=dest)

    def head_sha(self, repo: Path) -> str:
        """Return the commit id ``HEAD`` points at in ``repo``."""
        return _git("rev-parse", "--verify", "-q", "HEAD", cwd=repo).stdout.strip()

    def ref_sha(self, repo: Path, ref: str) -> str | None:
        """Return the commit ``ref`` points at in ``repo``, or ``None`` if it has none.

        An object-existence check would not do here: a push whose objects transferred
        but whose ref update was rejected leaves the commit present in the target while
        the branch still points elsewhere, so only the REF answers "is this applied?".
        """
        found = _git("rev-parse", "--verify", "-q", ref, cwd=repo, check=False)
        if found.returncode != 0:
            return None
        return found.stdout.strip() or None

    def default_branch(self, bare_repo: Path) -> str:
        """Return the branch ``HEAD`` points at in a bare repository."""
        return _git("symbolic-ref", "--short", "HEAD", cwd=bare_repo).stdout.strip()

    def push(self, worktree: Path, target: Path, branch: str) -> None:
        """Push ``worktree``'s ``HEAD`` to ``branch`` of ``target``."""
        _git("push", "-q", str(target), f"HEAD:{branch}", cwd=worktree)
