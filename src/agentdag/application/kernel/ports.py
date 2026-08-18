"""Ports the coordinator kernel needs: the clock, the journal, the run lock, the executor, the scope.

Every effect the kernel has on the world - reading the time, recording what happened,
holding the run directory, running a node, starting or killing the OS-level unit a
node runs under - goes through one of these seams (design 3.1, 3.3, 3.4, C8), so the
coordinator itself stays a deterministic program over typed records.

Contents:
    * :class:`Clock` - the ONE seam the kernel reads wall-clock time through.
    * :func:`format_stamp` - render an aware UTC datetime as the journal's timestamp format.
    * :func:`stamp` - read a clock and render its reading the same way.
    * :class:`Journal` - the append-only, replayable log of what a run has done.
    * :class:`RunDir` - the run directory's on-disk layout (state, journal, decisions, node work areas).
    * :class:`DecisionFileRef` - one decision file's identity, read from its filename alone.
    * :class:`RunLock` - the run directory's exclusive lock.
    * :class:`LockToken` - proof of a held lock, returned by :meth:`RunLock.acquire`.
    * :class:`ExecutorRequest` - everything an :class:`Executor` needs to run one node.
    * :class:`Executor` - runs one node's dispatch and reports its outcome.
    * :class:`ResolvedRow` - the model row and executor a spec resolves to.
    * :class:`Policy` - resolves a spec to a row and carries the run-wide limits.
    * :class:`IsolationScanner` - takes a content manifest of the run's isolation root.
    * :class:`Scope` - starts, probes and kills the OS-level unit a node runs under.
    * :class:`ScopeHandle` - identifies a unit a :class:`Scope` started.
    * :class:`KernelWiring` - everything one CLI invocation needs to run or resume a
      coordinator, built once by the composition root's ``wire_kernel`` (Task 17).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from ...domain.journal import JournalLine
    from ...domain.models import Decision, LockHolder, NodeOutcome, NodeSpec, RunState
    from ..graph_a_ports import GatePort, GitPort

__all__ = [
    "Clock",
    "DecisionFileRef",
    "Executor",
    "ExecutorRequest",
    "IsolationScanner",
    "Journal",
    "KernelWiring",
    "LockToken",
    "Policy",
    "ResolvedRow",
    "RunDir",
    "RunLock",
    "Scope",
    "ScopeHandle",
    "format_stamp",
    "stamp",
]


class Clock(Protocol):
    """The ONE seam the kernel reads wall-clock time through (design 3.3, O19)."""

    def now(self) -> datetime:
        """Return the current instant, tz-aware in UTC."""
        ...


def format_stamp(now: datetime) -> str:
    """Render an aware UTC datetime as the journal's timestamp format.

    A pure function over an already-read instant, so a caller that needs the SAME
    reading for a journal line's ``at`` and for a duration's start reads the clock
    once and formats that one reading, rather than reading it again here.

    Args:
        now: The instant to render; must be tz-aware and in UTC.

    Returns:
        ``YYYY-MM-DDTHH:MM:SS+00:00`` - seconds precision, an explicit UTC offset,
        never a trailing ``Z`` (design 3.3, O19; matches the pattern journal lines
        validate their ``at`` field against).

    Raises:
        ValueError: ``now`` is naive, or not UTC.

    Example:
        >>> from datetime import datetime, timezone
        >>> format_stamp(datetime(2026, 8, 17, 9, 12, 3, tzinfo=timezone.utc))
        '2026-08-17T09:12:03+00:00'
    """
    if now.tzinfo != timezone.utc:
        raise ValueError(f"clock reading is not UTC: {now!r}")
    return now.isoformat(timespec="seconds")


def stamp(clock: Clock) -> str:
    """Read ``clock`` and render that one reading as the journal's timestamp format.

    Args:
        clock: The clock to read.

    Returns:
        See :func:`format_stamp`.

    Raises:
        ValueError: ``clock.now()`` is naive, or not UTC.

    Example:
        >>> from datetime import datetime, timezone
        >>> class _FixedClock:
        ...     def now(self) -> datetime:
        ...         return datetime(2026, 8, 17, 9, 12, 3, tzinfo=timezone.utc)
        >>> stamp(_FixedClock())
        '2026-08-17T09:12:03+00:00'
    """
    return format_stamp(clock.now())


class Journal(Protocol):
    """The append-only, replayable log of what a run has done (design 3.1)."""

    def append(self, line: JournalLine) -> None:
        """Append ``line`` under the single-writer O_APPEND discipline; also copy it to the audit log."""
        ...

    def lines(self) -> list[JournalLine]:
        """Return every line the journal holds, parsed and typed, in file order."""
        ...


class RunDir(Protocol):
    """The run directory's on-disk layout: state, journal, decisions, node work areas (design 3.1).

    One run owns one directory (``root``); everything else is a path under it,
    created on demand by the method that names it. ``journal_path``,
    ``audit_path``, ``state_path`` and ``decisions_dir`` are plain attributes
    rather than methods because every caller needs the same fixed path, not a
    fresh one built from an argument.
    """

    root: Path
    journal_path: Path
    audit_path: Path
    state_path: Path
    decisions_dir: Path

    def node_dir(self, node_id: str, hash8: str) -> Path:
        """Return (creating it, owner-only) ``nodes/<node_id>/<hash8>/``."""
        ...

    def worktree(self, name: str) -> Path:
        """Return ``wt/<name>``; not created - the git port creates the worktree itself."""
        ...

    def intents_dir(self, kind: str) -> Path:
        """Return (creating it) ``intents/<kind>/``."""
        ...

    def marker(self, kind: str, key: str) -> Path:
        """Return ``done/<kind>/<key>``, creating the ``done/<kind>/`` directory."""
        ...

    def artefacts_dir(self) -> Path:
        """Return ``artefacts/``."""
        ...

    def manifest_path(self, map_id: str) -> Path:
        """Return ``manifest/<map_id>.json``."""
        ...

    def write_atomic(self, rel: str, text: str) -> Path:
        """Write ``text`` to ``rel`` (relative to ``root``) atomically, owner-only."""
        ...

    def read_text(self, rel: str) -> str:
        """Read ``rel`` (relative to ``root``) as UTF-8 text; creates nothing.

        A read-only counterpart to :meth:`write_atomic`, for a caller (a run-summary
        measurement, never a dispatch body) that must read a node's own artefact
        without composing the path from ``root`` itself, which only the port owns.

        Raises:
            FileNotFoundError: no such file exists.
        """
        ...

    def read_state(self) -> RunState:
        """Read and parse ``state_path``."""
        ...

    def write_state(self, state: RunState) -> None:
        """Write ``state_path`` atomically."""
        ...

    def read_decision(self, node_id: str, payload_hash: str) -> Decision | None:
        """Read this (node id, payload hash)'s decision, or ``None`` if none is recorded yet.

        Raises:
            RunRefused: the file exists but is unreadable, or its own content names a
                DIFFERENT ``payload_hash`` than the one asked for.
        """
        ...

    def write_decision(self, decision: Decision) -> None:
        """Publish ``decision`` write-once per (node id, payload hash); refuses to overwrite one.

        A decision, once recorded, is FINAL for that pair: a second write for the SAME
        (node id, payload hash) - a ``hold`` included - is refused, not replaced. Only a
        CHANGED payload gets asked again.

        Raises:
            ValueError: either half of ``decision`` could escape ``decisions/``.
            FileExistsError: this (node id, payload hash) already has a decision.
        """
        ...

    def decision_files(self) -> list[DecisionFileRef]:
        """Every decision file's (node id, short hash, path), sorted; reserved cancel files excluded.

        Identity only, read from each FILENAME - no file is opened. Lets a caller
        (the coordinator's ``fold_decisions``) skip a file it already folded before
        paying to parse it, so a file that becomes corrupted AFTER folding never
        blocks a later launch.
        """
        ...

    def read_decision_file(self, ref: DecisionFileRef) -> Decision:
        """Parse the decision at ``ref.path``, naming the path when it cannot be read.

        Raises:
            RunRefused: the file is empty or does not parse as a decision.
        """
        ...

    def list_decisions(self) -> list[Decision]:
        """Return every recorded decision in a deterministic order; reserved cancel files excluded."""
        ...


@dataclass(frozen=True, slots=True)
class DecisionFileRef:
    """One decision file's (node id, short hash) read from its FILENAME alone - no parse, no I/O.

    Returned by :meth:`RunDir.decision_files`, and handed straight back to
    :meth:`RunDir.read_decision_file` once a caller has decided the file is worth
    opening.
    """

    node_id: str
    short_hash: str
    path: Path


class RunLock(Protocol):
    """The run directory's exclusive lock: at most one live coordinator per run (design 3.4)."""

    def acquire(self, run_dir: Path, holder: LockHolder) -> LockToken:
        """Take the lock for ``run_dir``.

        Args:
            run_dir: The run directory to lock.
            holder: This process's identity, recorded as the lock's owner.

        Returns:
            Proof of the held lock.

        Raises:
            LockHeld: another live coordinator already holds ``run_dir``.
        """
        ...

    def release(self, token: LockToken) -> None:
        """Release a lock this process holds."""
        ...


@dataclass(frozen=True, slots=True)
class LockToken:
    """Proof of a held run-directory lock, handed back by :meth:`RunLock.acquire`."""

    path: Path
    holder: LockHolder


@dataclass(frozen=True, slots=True)
class ExecutorRequest:
    """Everything an :class:`Executor` needs to run one node's dispatch (design 2.1, C8)."""

    node_dir: Path
    cwd: Path
    brief: str
    prompt: str
    model: str
    effort: str | None
    max_turns: int
    isolation_root: Path
    write_set: tuple[str, ...]
    deny_bash: tuple[str, ...]


class Executor(Protocol):
    """Runs one node's dispatch against a prepared request and reports its outcome."""

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Run ``request`` to completion (or a suspend/needs-context outcome) and report it."""
        ...


@dataclass(frozen=True, slots=True)
class ResolvedRow:
    """What a tier policy resolves one spec to: a model row, and the executor that drives it."""

    alias: str
    """The model row's alias - the key tokens are measured and capped per (design 2.3, 3.4)."""

    executor: str
    """The name of the executor to run this node with, as keyed in the coordinator's executor map."""


class Policy(Protocol):
    """The tier policy: which model row a spec resolves to, and the run-wide executor limits.

    ``version`` is the content hash of the policy table a run was started under, recorded
    on the run and in its ``run_started`` line so a later run's records are comparable
    against the table they were produced by (design 2.3).
    """

    version: str
    max_turns: int
    deny_bash: tuple[str, ...]
    tokens_per_row: Mapping[str, int]

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve ``spec``'s tier role (and any explicit model) to the row and executor to use."""
        ...


class IsolationScanner(Protocol):
    """Takes a content manifest of a run's isolation root (design C8, the write-set net)."""

    def snapshot(self, root: Path) -> Mapping[str, str]:
        """Return relative POSIX path -> content hash for every file under ``root`` worth watching."""
        ...


class Scope(Protocol):
    """Starts, probes and kills the OS-level unit a node's executor runs under."""

    def start(self, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> ScopeHandle:
        """Start ``argv`` under a new scope named ``unit`` and return its handle."""
        ...

    def is_alive(self, handle: ScopeHandle) -> bool:
        """Return whether the unit ``handle`` names still has live processes."""
        ...

    def kill(self, handle: ScopeHandle) -> bool:
        """Kill the unit ``handle`` names.

        Returns:
            ``True`` only once the cgroup (or process) is verified gone.
        """
        ...


@dataclass(frozen=True, slots=True)
class ScopeHandle:
    """Identifies a unit a :class:`Scope` started."""

    unit: str
    pid: int


@dataclass(frozen=True, slots=True)
class KernelWiring:
    """Everything one CLI invocation needs to run or resume a coordinator (Task 17).

    Built once per invocation by the composition root's ``wire_kernel``, then handed
    straight into :func:`~agentdag.application.kernel.run.run_coordinator`'s keyword
    arguments - except :attr:`journal_factory`, which the CLI calls once the run
    directory's ``journal_path``/``audit_path`` are known, to build that run's own
    :class:`Journal`.

    The record lives in this module rather than the composition layer so an adapter
    (the CLI) can name the type it is handed without importing the composition root,
    which the layer contract forbids - the same reasoning
    :class:`~agentdag.application.graph_a_ports.GraphAWiring` documents for graph A.
    """

    journal_factory: Callable[[Path, Path], Journal]
    lock: RunLock
    clock: Clock
    executors: Mapping[str, Executor]
    gate_port: GatePort
    git: GitPort
    scanner: IsolationScanner
    policy: Policy
    scope: Scope
    runs_dir: Path
    parallel: int
