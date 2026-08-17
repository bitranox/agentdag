"""The run directory on disk: state, journal, decisions, node work areas (design 3.1).

One run owns one directory, created owner-only (``0700``) with its whole
first-level layout in place::

    <runs_base>/<run_id>/{decisions,intents,artefacts,wt,nodes,manifest,done}

Every write under the run dir goes through :meth:`FsRunDir.write_atomic` (or a
method that itself calls it), so a reader never observes a half-written file:
the write lands in a sibling temp file first - fsynced and closed - and only
then replaces the target with one atomic rename.

Contents:
    * :class:`FsRunDir` - the :class:`~agentdag.application.kernel.ports.RunDir` port over this layout.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path, PurePosixPath

from ...domain.models import Decision, RunState

__all__ = ["FsRunDir"]

_OWNER_ONLY_DIR = 0o700
_OWNER_ONLY_FILE = 0o600
_SUBDIRS = ("decisions", "intents", "artefacts", "wt", "nodes", "manifest", "done")


class FsRunDir:
    """RunDir port over a run's directory on disk: one directory per run, owner-only throughout."""

    def __init__(self, root: Path) -> None:
        """Bind to an existing run directory.

        Args:
            root: The run directory; use :meth:`create` to lay out a fresh one,
                or :meth:`open` to bind to one that already exists.
        """
        self.root = root
        self.journal_path = root / "journal.jsonl"
        self.audit_path = root / "audit.jsonl"
        self.state_path = root / "state.json"
        self.decisions_dir = root / "decisions"

    @classmethod
    def create(cls, runs_base: Path, run_id: str) -> FsRunDir:
        """Lay out a fresh run directory under ``runs_base``, owner-only throughout.

        Args:
            runs_base: The directory holding every run.
            run_id: This run's id; the directory is ``runs_base / run_id``.

        Returns:
            A run dir bound to the new directory, its first-level layout already in place.

        Raises:
            FileExistsError: ``runs_base / run_id`` already exists - a run id is
                claimed once, never reused.
        """
        root = runs_base / run_id
        root.mkdir(mode=_OWNER_ONLY_DIR)
        for name in _SUBDIRS:
            (root / name).mkdir(mode=_OWNER_ONLY_DIR)
        return cls(root)

    @classmethod
    def open(cls, runs_base: Path, run_id: str) -> FsRunDir:
        """Bind to a run directory that must already exist.

        Args:
            runs_base: The directory holding every run.
            run_id: This run's id; the directory is ``runs_base / run_id``.

        Returns:
            A run dir bound to ``runs_base / run_id``.

        Raises:
            FileNotFoundError: no such run directory exists.
        """
        root = runs_base / run_id
        if not root.is_dir():
            raise FileNotFoundError(f"run dir does not exist: {root}")
        return cls(root)

    def node_dir(self, node_id: str, hash8: str) -> Path:
        """Return (creating it, owner-only) ``nodes/<node_id>/<hash8>/``.

        Args:
            node_id: The node's id; may contain ``@`` (a continuation counter,
                e.g. ``w_migrate@1``) but never a path separator or ``..``.
            hash8: The node's content-addressed key, truncated to 8 hex characters.

        Returns:
            The created directory.

        Raises:
            ValueError: ``node_id`` contains ``/``, ``\\`` or ``..`` - a path
                traversal attempt rather than a real node id.
        """
        self._validate_node_id(node_id)
        node_root = self.root / "nodes" / node_id
        node_root.mkdir(mode=_OWNER_ONLY_DIR, exist_ok=True)
        leaf = node_root / hash8
        leaf.mkdir(mode=_OWNER_ONLY_DIR, exist_ok=True)
        return leaf

    def worktree(self, name: str) -> Path:
        """Return ``wt/<name>``; not created - the git port creates the worktree itself."""
        return self.root / "wt" / name

    def intents_dir(self, kind: str) -> Path:
        """Return (creating it) ``intents/<kind>/``."""
        path = self.root / "intents" / kind
        path.mkdir(mode=_OWNER_ONLY_DIR, exist_ok=True)
        return path

    def marker(self, kind: str, key: str) -> Path:
        """Return ``done/<kind>/<key>``, creating the ``done/<kind>/`` directory.

        The marker file itself is not created here; its existence is whatever
        the caller writes (or does not write) to the returned path.
        """
        directory = self.root / "done" / kind
        directory.mkdir(mode=_OWNER_ONLY_DIR, exist_ok=True)
        return directory / key

    def artefacts_dir(self) -> Path:
        """Return ``artefacts/`` (already created by :meth:`create`)."""
        return self.root / "artefacts"

    def manifest_path(self, map_id: str) -> Path:
        """Return ``manifest/<map_id>.json`` (the ``manifest/`` dir is already created by :meth:`create`)."""
        return self.root / "manifest" / f"{map_id}.json"

    def write_atomic(self, rel: str, text: str) -> Path:
        """Write ``text`` to ``rel`` under :attr:`root`, atomically and owner-only.

        The write lands in a sibling temp file first (fsynced and closed),
        which then replaces the target with one atomic rename - a reader never
        observes a partial write.

        Args:
            rel: A POSIX-style path relative to :attr:`root`; its parent
                directories are created if missing.
            text: The content to write, as UTF-8.

        Returns:
            The target path.

        Raises:
            ValueError: ``rel`` is absolute or escapes :attr:`root` via ``..``.
        """
        target = self._resolve_rel(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".tmp-", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            if sys.platform != "win32":
                os.fchmod(tmp.fileno(), _OWNER_ONLY_FILE)
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        Path(tmp.name).replace(target)
        return target

    def read_state(self) -> RunState:
        """Read and parse :attr:`state_path`."""
        return RunState.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, state: RunState) -> None:
        """Write :attr:`state_path` atomically."""
        self.write_atomic("state.json", state.model_dump_json(indent=1))

    def read_decision(self, node_id: str) -> Decision | None:
        """Read ``decisions/<node_id>.json``, or ``None`` if no decision is recorded yet."""
        self._validate_node_id(node_id)
        path = self.decisions_dir / f"{node_id}.json"
        if not path.is_file():
            return None
        return Decision.model_validate_json(path.read_text(encoding="utf-8"))

    def write_decision(self, decision: Decision) -> None:
        """Write ``decisions/<node_id>.json`` once; refuses to overwrite an existing one.

        The filename is reserved with an exclusive create BEFORE any content is
        written, so two callers racing to decide the same node can never both
        succeed - the loser's ``os.open`` raises before it writes anything
        (design 3.4). The reserved (empty) file is then replaced, in one
        atomic rename, by the real content written through :meth:`write_atomic`.

        Args:
            decision: The decision to record; keyed by ``decision.node_id``.

        Raises:
            FileExistsError: a decision for this node id is already recorded.
        """
        self._validate_node_id(decision.node_id)
        final = self.decisions_dir / f"{decision.node_id}.json"
        fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _OWNER_ONLY_FILE)
        os.close(fd)
        self.write_atomic(f"decisions/{decision.node_id}.json", decision.model_dump_json(indent=1))

    @staticmethod
    def _validate_node_id(node_id: str) -> None:
        """Refuse a ``node_id`` that could escape its directory (design 3.1's traversal guard).

        Raises:
            ValueError: ``node_id`` contains ``/``, ``\\`` or ``..``.
        """
        if "/" in node_id or "\\" in node_id or ".." in node_id:
            raise ValueError(f"unsafe node id: {node_id!r}")

    def _resolve_rel(self, rel: str) -> Path:
        """Resolve a POSIX-style relative path under :attr:`root`, refusing to escape it.

        Raises:
            ValueError: ``rel`` is absolute or contains a ``..`` component.
        """
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"unsafe relative path: {rel!r}")
        return self.root.joinpath(*pure.parts)
