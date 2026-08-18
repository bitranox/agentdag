"""The run directory on disk: state, journal, decisions, node work areas (design 3.1).

One run owns one directory, created owner-only (``0700``) with its whole
first-level layout in place::

    <runs_base>/<run_id>/{decisions,intents,artefacts,wt,nodes,manifest,done}

Every write under the run dir lands in a sibling temp file first - fsynced
and closed - and only then becomes visible in one atomic filesystem call, so
a reader never observes a half-written file. Most writes (:meth:`FsRunDir.write_atomic`,
and :meth:`~FsRunDir.write_state` through it) publish the temp file with an
atomic rename. :meth:`FsRunDir.write_decision` publishes it with an atomic
hard link instead, so a decision file is never briefly empty (see its
docstring for why that distinction matters).

Contents:
    * :class:`FsRunDir` - the :class:`~agentdag.application.kernel.ports.RunDir` port over this layout.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath

from ...application.kernel.ports import DecisionFileRef
from ...domain.errors import RunRefused
from ...domain.keys import hash8
from ...domain.models import Decision, RunState

__all__ = ["FsRunDir"]

_OWNER_ONLY_DIR = 0o700
_OWNER_ONLY_FILE = 0o600
_SUBDIRS = ("decisions", "intents", "artefacts", "wt", "nodes", "manifest", "done")
_HEX8 = re.compile(r"[0-9a-f]{8}")
"""The shape a payload hash must shorten to before it may name a decision file."""


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
        root.mkdir(mode=_OWNER_ONLY_DIR)  # no exist_ok: a reused run id must raise, not silently succeed
        self = cls(root)
        for name in _SUBDIRS:
            self._mkdir_owner_only(root / name)
        return self

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
        return self._mkdir_owner_only(self.root / "nodes" / node_id / hash8)

    def worktree(self, name: str) -> Path:
        """Return ``wt/<name>``; not created - the git port creates the worktree itself."""
        return self.root / "wt" / name

    def intents_dir(self, kind: str) -> Path:
        """Return (creating it) ``intents/<kind>/``."""
        return self._mkdir_owner_only(self.root / "intents" / kind)

    def marker(self, kind: str, key: str) -> Path:
        """Return ``done/<kind>/<key>``, creating the ``done/<kind>/`` directory.

        The marker file itself is not created here; its existence is whatever
        the caller writes (or does not write) to the returned path.
        """
        directory = self._mkdir_owner_only(self.root / "done" / kind)
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
        self._mkdir_owner_only(target.parent)
        tmp_path = self._write_temp_file(target.parent, text)
        try:
            tmp_path.replace(target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return target

    def read_text(self, rel: str) -> str:
        """Read ``rel`` under :attr:`root` as UTF-8 text; creates nothing.

        Args:
            rel: A POSIX-style path relative to :attr:`root`.

        Returns:
            The file's content.

        Raises:
            ValueError: ``rel`` is absolute or escapes :attr:`root` via ``..``.
            FileNotFoundError: no such file exists.
        """
        return self._resolve_rel(rel).read_text(encoding="utf-8")

    def read_state(self) -> RunState:
        """Read and parse :attr:`state_path`.

        Raises:
            RunRefused: the file exists but fails to parse - a crash-corrupted state
                file must never surface as a bare pydantic error deep inside a resume;
                naming the path here is what :meth:`read_decision` already does for
                the same failure shape. A MISSING file still raises the plain
                ``FileNotFoundError`` reading it produces - every caller already knows
                whether ``state_path`` is supposed to exist.
        """
        text = self.state_path.read_text(encoding="utf-8")
        try:
            return RunState.model_validate_json(text)
        except ValueError as exc:
            raise RunRefused(f"state file {self.state_path} is unreadable: {exc}") from exc

    def write_state(self, state: RunState) -> None:
        """Write :attr:`state_path` atomically."""
        self.write_atomic("state.json", state.model_dump_json(indent=1))

    def read_decision(self, node_id: str, payload_hash: str) -> Decision | None:
        """Read this (node id, payload hash)'s decision, or ``None`` if none is recorded yet.

        Args:
            node_id: The approve node the decision answers.
            payload_hash: The content hash of the payload it was made for; the
                other half of a decision's identity, and part of its filename.

        Raises:
            ValueError: ``node_id`` could escape ``decisions/``, or ``payload_hash``
                does not shorten to eight hex characters.
            RunRefused: the file exists but is empty or fails to parse. A
                crash-corrupted decision file must never read as "no decision
                yet" - a reader would silently drop a decision that a
                previous, interrupted :meth:`write_decision` reserved, and a
                later :meth:`write_decision` would then refuse forever
                without anyone knowing why (design 3.4's write-once
                contract). Also raised when the file parses fine but its OWN
                ``payload_hash`` names something OTHER than the argument - the
                short hash in the filename is only 8 hex characters, so a
                content check is what tells a genuine answer from one that
                merely landed at the same truncated name.
        """
        path = self._decision_path(node_id, payload_hash)
        if not path.is_file():
            return None
        decision = self._parse_decision(path)
        if decision.payload_hash != payload_hash:
            raise RunRefused(
                f"decision file {path} names payload_hash {decision.payload_hash!r}, not the requested {payload_hash!r}"
            )
        return decision

    def write_decision(self, decision: Decision) -> None:
        """Publish ``decision`` as ``decisions/<node_id>.<hash8(payload_hash)>.json``, once.

        Write-once is per (node id, PAYLOAD hash), not per node id: an approve node
        whose payload changed is asking a new question, and its answer is a new file
        beside the old one rather than an overwrite of it (design 3.4's binding, the
        idempotency key D2 took from DBOS's ``send(..., idempotency_key)``). The old
        file stays as the record of what was answered about the old payload.

        A decision, once recorded, is FINAL for that pair: this raises
        ``FileExistsError`` on any SECOND write for the SAME (node id, payload hash) -
        a ``hold`` included - there is no revise-the-last-verdict path, only a fresh
        payload's own decision. The world has to change (a different payload) before
        the question is asked again; the same payload never gets asked twice.

        The content is written to a fully-formed, fsynced temp file FIRST,
        then published with :func:`os.link` - a hard link is atomic and
        raises ``FileExistsError`` when the target already exists, so the
        reader-visible path is always either absent or complete, never an
        empty stub. This replaces an earlier design that reserved the final
        path with an empty ``O_EXCL`` create before writing content: a crash
        in that window left an empty file behind that :meth:`read_decision`
        could not parse, and every later :meth:`write_decision` for that node
        id refused forever, mistaking the stub for a real decision. The temp
        file is removed afterward regardless of outcome.

        Note:
            ``os.link`` needs a filesystem that supports hard links (NTFS on
            Windows; every POSIX filesystem this project targets). If the
            filesystem does not support it, ``os.link`` raises ``OSError``,
            which is left to propagate rather than silently falling back to
            a non-atomic write.

        Args:
            decision: The decision to record; keyed by ``decision.node_id`` AND
                ``decision.payload_hash``, both of which name its file. Pydantic
                already refuses one built with no ``payload_hash`` - it would name no
                payload and have half an identity - so there is nothing to check here.

        Raises:
            ValueError: either half of ``decision`` could escape ``decisions/`` -
                ``decision.node_id`` contains a path separator or ``..``, or
                ``decision.payload_hash`` does not shorten to eight hex characters.
            FileExistsError: this (node id, payload hash) already has a decision.
        """
        final = self._decision_path(decision.node_id, decision.payload_hash)
        tmp_path = self._write_temp_file(self.decisions_dir, decision.model_dump_json(indent=1))
        try:
            os.link(tmp_path, final)
        finally:
            tmp_path.unlink(missing_ok=True)

    def decision_files(self) -> list[DecisionFileRef]:
        """Every decision file's (node id, short hash, path), sorted; reserved cancel files excluded.

        Identity only, read from each FILENAME - no file is opened. Lets a caller (the
        coordinator's ``fold_decisions``) decide which files it has already folded
        BEFORE paying to parse them, so a file that becomes corrupted AFTER folding
        never blocks a later launch.

        Sorted rather than in directory order, so folding the same ``decisions/``
        directory twice appends its journal lines in the same order both times -
        directory order is filesystem-dependent and would make a replay's line order
        depend on the machine it ran on.

        ``decisions/`` also holds two RESERVED files that are not decisions, both
        written by M3: ``<node_id>.cancel.json`` (a per-node cancel) and
        ``_run.cancel.json`` (a whole-run cancel). Both end ``.cancel.json``, so a
        single suffix check skips either shape without trying to parse it as a
        :class:`~agentdag.domain.models.Decision`. A decision's own second name
        component is eight HEX characters, so it can never read as ``cancel``.

        Returns:
            Every decision file's identity, ascending by filename.
        """
        return [
            self._decision_file_ref(path)
            for path in sorted(self.decisions_dir.glob("*.json"))
            if not path.name.endswith(".cancel.json")
        ]

    @staticmethod
    def _decision_file_ref(path: Path) -> DecisionFileRef:
        """Split ``<node_id>.<short_hash>.json`` from ``path``'s name alone; no I/O.

        ``rpartition`` takes the LAST ``.``-separated component as the short hash and
        everything before it as the node id, so a node id that itself contains a
        literal ``.`` still splits correctly - a decision's short hash is always
        exactly eight hex characters, which a node id segment never is by construction
        (:meth:`_short_hash` only ever produces one from :meth:`write_decision`).
        """
        stem = path.name.removesuffix(".json")
        node_id, _, short_hash = stem.rpartition(".")
        return DecisionFileRef(node_id=node_id, short_hash=short_hash, path=path)

    def read_decision_file(self, ref: DecisionFileRef) -> Decision:
        """Parse the decision at ``ref.path``, naming the path when it cannot be read.

        Raises:
            RunRefused: the file is empty or does not parse as a decision.
        """
        return self._parse_decision(ref.path)

    def list_decisions(self) -> list[Decision]:
        """Return every recorded decision, parsed, in :meth:`decision_files`'s order.

        Returns:
            Every parsed decision, ascending by filename.

        Raises:
            RunRefused: one of the files exists but is empty or fails to parse.
        """
        return [self.read_decision_file(ref) for ref in self.decision_files()]

    @staticmethod
    def _parse_decision(path: Path) -> Decision:
        """Parse one decision file, naming the path when it cannot be read.

        Raises:
            RunRefused: the file is empty or does not parse as a decision.
        """
        try:
            return Decision.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise RunRefused(f"decision file {path} is unreadable: {exc}") from exc

    def _decision_path(self, node_id: str, payload_hash: str) -> Path:
        """Return ``decisions/<node_id>.<hash8(payload_hash)>.json``, both halves validated.

        Raises:
            ValueError: ``node_id`` contains a path separator or ``..``, or
                ``payload_hash`` does not shorten to eight hex characters (which a
                traversal attempt through the hash never does).
        """
        self._validate_node_id(node_id)
        return self.decisions_dir / f"{node_id}.{self._short_hash(payload_hash)}.json"

    @staticmethod
    def _short_hash(payload_hash: str) -> str:
        """Shorten ``payload_hash`` to the eight hex characters that name a decision file.

        Raises:
            ValueError: the short form is not eight hex characters - the hash is not a
                ``sha256:<hex>`` content hash, and letting an arbitrary string name a
                file is how a ``..`` reaches the path.
        """
        short = hash8(payload_hash)
        if _HEX8.fullmatch(short) is None:
            raise ValueError(f"unsafe payload hash: {payload_hash!r}")
        return short

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

    def _mkdir_owner_only(self, target: Path) -> Path:
        """Create ``target`` and any missing directories between it and :attr:`root`, each owner-only (``0700``).

        ``Path.mkdir(parents=True, mode=...)`` only applies ``mode`` to the
        leaf directory - any missing parent is created at the platform
        default (subject to umask), which can leave an intermediate level
        group- or other-readable. This walks from :attr:`root` (already
        owner-only) down to ``target``, creating each missing level
        explicitly at ``0700`` and tolerating a level that already exists (an
        earlier call already created it, or a sibling node shares it).

        Args:
            target: A directory at or below :attr:`root`; may equal
                :attr:`root` itself.

        Returns:
            ``target``.
        """
        current = self.root
        for part in target.relative_to(self.root).parts:
            current = current / part
            with contextlib.suppress(FileExistsError):
                current.mkdir(mode=_OWNER_ONLY_DIR)
        return current

    def _write_temp_file(self, directory: Path, text: str) -> Path:
        """Write ``text`` to a fresh owner-only temp file in ``directory``, fsynced and closed.

        On any failure while creating, chmoding, writing, flushing or
        fsyncing the temp file, the temp file is removed before the
        exception propagates - a caller never has to clean up a leaked
        ``.tmp-*`` file from this step.

        Args:
            directory: The directory to create the temp file in; must
                already exist.
            text: The content to write, as UTF-8.

        Returns:
            The path of the fsynced, closed temp file.
        """
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=".tmp-", delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                tmp_path = Path(tmp.name)
                if sys.platform != "win32":
                    os.fchmod(tmp.fileno(), _OWNER_ONLY_FILE)
                tmp.write(text)
                tmp.flush()
                os.fsync(tmp.fileno())
        except BaseException:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise
        return tmp_path
