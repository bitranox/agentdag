"""Ports graph A needs. Adapters implement them; tests inject fakes at these seams.

Every effect graph A has on the world goes through one of these five protocols, so
the graph itself stays a deterministic program over typed records. The tests use the
real adapters over temporary git repositories for everything except :class:`WorkPort`,
which is the one genuinely external edge (a model call).

Contents:
    * :class:`GitPort` - clone, inspect and push repositories.
    * :class:`GatePort` - run the mechanical gate and return its exit code.
    * :class:`WorkPort` - run one work node against a worktree.
    * :class:`ApprovePort` - ask a human before anything leaves the process.
    * :class:`RunStore` - where a run keeps its worktrees, logs and records.
    * :class:`GraphAWiring` - the five of them, as one run uses them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ..domain.graph_a import WorkResult

__all__ = ["ApprovePort", "GatePort", "GitPort", "GraphAWiring", "RunStore", "WorkPort"]


class GitPort(Protocol):
    """Every git operation graph A performs."""

    def mirror(self, source: Path, dest: Path) -> None:
        """Create a bare mirror of ``source`` at ``dest``, reading ``source`` only."""
        ...

    def remove_mirror(self, dest: Path) -> None:
        """Delete a mirror at ``dest``, read-only object files included."""
        ...

    def remove_tree(self, path: Path) -> None:
        """Delete a working tree at ``path``, read-only object files included.

        Distinct from :meth:`remove_mirror` only in what it is called on: a plain
        clone's ``.git/objects/**`` is written read-only exactly like a mirror's, so
        the same read-only-tolerant removal applies to a staging worktree.
        """
        ...

    def clone(self, origin: Path, dest: Path) -> None:
        """Clone ``origin`` into a working tree at ``dest``."""
        ...

    def head_sha(self, repo: Path) -> str:
        """Return the commit id ``HEAD`` points at in ``repo``."""
        ...

    def ref_sha(self, repo: Path, ref: str) -> str | None:
        """Return the commit ``ref`` points at in ``repo``, or ``None`` if it has none."""
        ...

    def default_branch(self, bare_repo: Path) -> str:
        """Return the branch ``HEAD`` points at in a bare repository."""
        ...

    def push(self, worktree: Path, target: Path, branch: str) -> None:
        """Push ``worktree``'s ``HEAD`` to ``branch`` of ``target``."""
        ...


class GatePort(Protocol):
    """The mechanical verification step: something the agent cannot talk its way past."""

    def run(self, worktree: Path, log: Path) -> int:
        """Run the gate under the host-wide lock; return its exit code."""
        ...


class WorkPort(Protocol):
    """One work node: make the change the brief describes inside ``worktree``."""

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        """Run the node and report what it did as a typed record, never as prose."""
        ...


class ApprovePort(Protocol):
    """The human in the loop, asked once before anything is pushed."""

    def confirm(self, prompt: str) -> bool:
        """Show ``prompt`` and return whether the operator approved."""
        ...


class RunStore(Protocol):
    """Where one run keeps its worktrees, logs, agent homes, records and done markers."""

    root: Path
    """The directory this run owns; everything else is relative to it."""

    def worktree(self, name: str) -> Path:
        """Return the working-tree path for the branch called ``name``."""
        ...

    def log(self, name: str) -> Path:
        """Return the log-file path called ``name``."""
        ...

    def home(self, name: str) -> Path:
        """Return (creating it) an isolated home directory for the node called ``name``."""
        ...

    def write_json(self, rel: str, text: str) -> None:
        """Write ``text`` to ``rel`` under the run root, creating parent directories."""
        ...

    def marker(self, key: str) -> Path:
        """Return the done-marker path for ``key``; its existence means already applied."""
        ...


@dataclass(frozen=True, slots=True)
class GraphAWiring:
    """The five port implementations one graph A run uses.

    The record lives here rather than in the composition layer so an adapter (the CLI)
    can name the type it is handed without importing the composition root, which the
    layer contract forbids. Composition still owns the choice of implementations.
    """

    git: GitPort
    gate: GatePort
    work: WorkPort
    approve: ApprovePort
    store: RunStore
