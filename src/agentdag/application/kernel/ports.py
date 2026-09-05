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
    * :class:`LaunchResult` - whether a background launch proved itself within
      :meth:`Scope.confirm`'s timeout, and any stderr it captured.
    * :class:`Sandbox`, :class:`SandboxRequest`, :class:`SandboxGuarantees` - re-exported
      from :mod:`.sandbox` (Task 19) so every port the kernel needs is reachable from this
      one module, matching every port above; the classes themselves are defined there (or,
      for ``SandboxGuarantees``, in :mod:`~agentdag.domain.models` and re-exported by
      ``.sandbox`` in turn), not duplicated here. ``SandboxGuarantees`` travels with
      ``Sandbox`` for the same reason ``SandboxRequest`` does: an adapter implementing the
      port needs the type its own :meth:`~.sandbox.Sandbox.guarantees` method returns.
    * :class:`KernelWiring` - everything one CLI invocation needs to run or resume a
      coordinator, built once by the composition root's ``wire_kernel`` (Task 17).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import TYPE_CHECKING, Protocol

from ...domain.models import MarkerPhase  # runtime: it is a default argument value, not just an annotation
from .sandbox import Sandbox, SandboxGuarantees, SandboxRequest

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from ...domain.journal import JournalLine
    from ...domain.models import CredentialVerdict, Decision, LockHolder, NodeOutcome, NodeSpec, RetryGrant, RunState
    from ...domain.policy import FailureAction, RunLimits
    from ..graph_a_ports import GatePort, GitPort
    from .notify import Notifier
    from .registry import OpRegistry

__all__ = [
    "Clock",
    "CredentialProbe",
    "DecisionFileRef",
    "Executor",
    "ExecutorRequest",
    "IsolationScanner",
    "Journal",
    "KernelWiring",
    "LaunchResult",
    "LockToken",
    "PathResolver",
    "Policy",
    "ProbeFinding",
    "ResolvedRow",
    "RetryGrantFileRef",
    "RunDir",
    "RunLock",
    "Sandbox",
    "SandboxGuarantees",
    "SandboxRequest",
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
    ``audit_path``, ``state_path``, ``decisions_dir`` and ``retries_dir`` are plain
    attributes rather than methods because every caller needs the same fixed path, not a
    fresh one built from an argument.
    """

    root: Path
    journal_path: Path
    audit_path: Path
    state_path: Path
    decisions_dir: Path
    retries_dir: Path

    def node_dir(self, node_id: str, hash8: str) -> Path:
        """Return (creating it, owner-only) ``nodes/<node_id>/<hash8>/``."""
        ...

    def worktree(self, name: str) -> Path:
        """Return ``wt/<name>``; not created - the git port creates the worktree itself."""
        ...

    def intents_dir(self, kind: str) -> Path:
        """Return (creating it) ``intents/<kind>/``."""
        ...

    def marker(self, kind: str, key: str, *, phase: MarkerPhase = MarkerPhase.DONE) -> Path:
        """Return ``<phase>/<kind>/<key>``, creating the ``<phase>/<kind>/`` directory."""
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

    def write_retry_grant(self, grant: RetryGrant) -> None:
        """Publish ``grant`` write-once per (node id, key); refuses a second write for the same pair.

        One grant buys exactly one attempt, so a doubled ``run retry`` must refuse rather
        than mint a second. A LATER failure of the same node is a different key and so a
        different file, which is what lets an operator grant again after a granted attempt
        fails.

        Raises:
            ValueError: ``grant.node_id`` could escape ``retries/``, or ``grant.key`` does
                not shorten to eight hex characters.
            FileExistsError: this (node id, key) already has a grant.
        """
        ...

    def retry_grant_files(self) -> list[RetryGrantFileRef]:
        """Every retry grant file's (node id, short hash, path), sorted; no file is opened."""
        ...

    def read_retry_grant_file(self, ref: RetryGrantFileRef) -> RetryGrant:
        """Parse the grant at ``ref.path``, naming the path when it cannot be read.

        Raises:
            RunRefused: the file is empty or does not parse as a retry grant.
        """
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


@dataclass(frozen=True, slots=True)
class RetryGrantFileRef:
    """One retry grant file's (node id, short hash) read from its FILENAME alone - no parse, no I/O.

    ``short_hash`` is ``hash8`` of the granted journal KEY, which is what the coordinator
    compares against the keys it has already folded, so a grant file that becomes unreadable
    after folding never blocks a later launch.
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
    extra_roots: tuple[Path, ...] = ()
    """Roots BESIDES :attr:`isolation_root` this node may work in - an operator-supplied
    workspace the run does not own. Empty is the shape every dispatch had before this field
    existed: the run root is then the only root, and nothing outside it is reachable.

    A SECOND root rather than a wider first one, because the two are governed differently.
    Everything under :attr:`isolation_root` is addressed root-relatively - the write set, the
    isolation scan's manifest, a node's ``artefact_refs`` - and a path outside it has no such
    form at all. So a path under an extra root is addressed by its ABSOLUTE POSIX form
    wherever those three would use a relative one. That keeps a grant ANCHORED on a literal
    segment unconfusable - ``wt/a/**`` cannot match a path starting with ``/`` - but it is not
    what bounds an UNANCHORED one: :func:`~agentdag.domain.scan.is_covered` translates a
    trailing ``**`` to an fnmatch ``*``, whose ``*`` spans ``/``, so a bare ``**`` matches an
    absolute path too. What keeps naming a root from widening a write set is the root
    containment that :func:`~agentdag.adapters.kernel.hooks_claude.deny_outside_write_set`
    applies beside the grant list, not the shape of the globs.

    Every entry must be ABSOLUTE and already resolved (``expanduser().resolve()``). That is
    what lets :func:`~agentdag.adapters.kernel.executor_claude.allowed_writes` state the grant
    as ``<root>/**`` while
    :func:`~agentdag.adapters.kernel.hooks_claude.deny_outside_write_set` compares a
    ``realpath``-resolved target against it: an unresolved root would make the two disagree
    and deny every write in the very directory the node was given.

    A root here is NOT watched by the isolation scan
    (:meth:`~agentdag.application.kernel.context.Coordinator.scan`), which diffs the run root
    alone. That is the cost of the second root and it is stated where the scan records it, not
    only here."""

    read_roots: tuple[Path, ...] | None = None
    """The directories this node may READ inside, or ``None`` to leave reads unconfined.

    ``None`` and an empty tuple are different answers and both are reachable: ``None`` is
    "this call site does not confine reads", the behaviour every node had before this field
    existed, while ``()`` is "confined to nothing", which denies every read. A caller that
    means to confine a node passes the directories it may see, and an executor that
    supports confinement must also refuse the tools whose reads it cannot attribute - a
    shell command's read set is not decidable from its text."""

    deny_tools: tuple[str, ...] = ()
    """Tool names this node may not call at all - refused by a ``PreToolUse`` hook, whatever
    the arguments (``[kernel] deny_tools``; shipped default ``WebFetch``, ``WebSearch`` and
    ``Task``, the tools that reach the network or spawn a sub-agent). An empty tuple here
    means the executor's own fallback applies, the same reading as :attr:`deny_bash`; a
    caller that means to close nothing passes an empty tuple to an executor built with an
    empty one. Defaults so a call site that predates this field constructs without it."""

    token_cap: int | None = None
    """This node's own token cap for its resolved row (``NodeSpec.budget.tokens[row]``),
    or ``None`` when the node declares no cap for this row - nothing for the executor to
    enforce at the turn seam (design 7, M3). Defaults to ``None`` so every call site and
    test fixture that predates this field still constructs without naming it."""

    handover_at_tokens: int | None = None
    """This node's CONTEXT ceiling for its resolved row (``TierRow.handover_at_tokens``),
    or ``None`` when the row declares none - nothing checked (design 3.8).

    A THIRD quantity at the same turn seam, and the one most easily confused with
    :attr:`token_cap`. This is the size of ONE turn's own context - what the model just
    saw, ``input_total`` of that turn's usage - never a sum across turns. A context
    ceiling asks "is the window full right now", a question a running sum cannot answer:
    the sum only grows, so it would trip on a long dispatch whose window is nowhere near
    full.

    Crossing it is NOT a failure and NOT a budget event. The node ends
    ``needs_continuation``, KEEPS its artefact refs, and a successor continues the work
    from the same worktree with ``continuation + 1``. Bound the chain with
    ``Policy.max_continuations``, never by making this a hard stop."""

    is_stopping: Callable[[], bool] | None = None
    """Whether this node's SUBTREE has been asked to stop, or ``None`` for a call site that
    names no subtree (every fixture predating this field, and any dispatch outside a plan).

    A PREDICATE, never a bool: the subtree decides to stop while its nodes are already in
    flight, so a dispatch that read the value once before its first turn would be armed
    either never or always. It is read at the same turn seam the context ceiling is, and
    the two are ORed into ONE arming decision - crossing a ceiling and having your subtree
    stopped are different reasons to hand over, but the handover itself, and the measured
    grace it gets (``HANDOVER_GRACE_TURNS``), are the same mechanism.

    Bound by the caller to one node: the kernel's ``StopScope`` answers per node id, and
    binding it here keeps the executor from knowing that type exists at all."""

    deadline_s: float | None = None
    """This node's own wall-clock deadline (``NodeSpec.deadline_s``, already clamped to
    ``Policy.deadline_ceiling_s`` by :meth:`~agentdag.application.kernel.context.Coordinator.work`),
    or ``None`` for a call site that predates this field (every test fixture built before
    M3). A DIFFERENT unit and a DIFFERENT ceiling from :attr:`token_cap`: this is ELAPSED
    WALL-CLOCK SECONDS since the dispatch started, checked at the same turn seam the token
    cap is (design 7, M3; ``workflow/design/probes/m3-interrupt.md`` in RESEARCH) - never
    conflate the two comparisons, which is exactly the mistake Task 20 already made once
    with a single-quantity turn seam."""


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

    handover_at_tokens: int
    """This row's CONTEXT ceiling in tokens (design 3.8): past this much context in a
    single turn, a node on this row writes a handover and ends ``needs_continuation``.

    It lives on the ROW, not on the node spec and not in the run limits, because how full
    a context window is depends on WHICH window - a figure that means "about half" on a
    1M-token row means "over budget" on a 200k one. A node cannot declare it and a run
    limit cannot clamp it. Required rather than optional: every row in the tier table
    carries one (``TierRow.handover_at_tokens``), so an adapter that cannot supply it is
    resolving something that is not a tier row."""


@dataclass(frozen=True, slots=True)
class Denylists:
    """The two lists that ARE the kernel's stated boundary for what a node may call.

    ``bash`` filters Bash commands by substring; ``tools`` closes whole tools outright.
    Both come from the app config (``[kernel] deny_bash`` / ``deny_tools``) and reach every
    dispatch through the loaded policy. Bundled so a policy constructor names the boundary
    once instead of growing a parameter per list.
    """

    bash: tuple[str, ...]
    tools: tuple[str, ...]


class Policy(Protocol):
    """The tier policy: which model row a spec resolves to, and the run-wide executor limits.

    ``version`` is the content hash of the policy table a run was started under, recorded
    on the run and in its ``run_started`` line so a later run's records are comparable
    against the table they were produced by (design 2.3).
    """

    version: str
    max_turns: int

    default_node_tokens: int | None
    """The per-node token budget applied when a node's own spec declares none, or ``None``
    to leave such a node uncapped.

    Planner-emitted entries carry no ``budget`` - nothing in the plan schema makes one
    mandatory and no shipped rule adds one - so before this existed EVERY node on the
    model-driven path was exempt from the per-node cap, and only the run-wide row ceiling
    bound them at all. That is the gap `OPEN-WORK.md` 55 names. The default closes it in the
    fail-CLOSED direction: an absent budget becomes a real number rather than an exemption.

    In the unit ``charged_tokens`` carries: input + cache_creation + output, cache reads
    excluded."""
    max_attempts: int

    max_continuations: int
    """How many handovers one node chain may take before it ends
    ``failed``/``continuation_limit`` (design 3.8). Bounds a chain the way
    :attr:`max_attempts` bounds retries, and is the ONLY thing that does: a context
    ceiling is not a failure, so nothing else about a handover ever stops one."""
    """How many times ONE code node may be dispatched before a transient failure stands
    (``Thresholds.max_attempts``). Read by :meth:`Coordinator._dispatch
    <agentdag.application.kernel.context.Coordinator._dispatch>`; ``1`` disables retrying.
    A model node is NOT retried here - design 2.3 rule 5 owns that, and it escalates a rank
    rather than repeating in place."""
    deny_bash: tuple[str, ...]
    deny_tools: tuple[str, ...]
    """Tool names every node is refused outright (``[kernel] deny_tools``), forwarded to each
    dispatch beside :attr:`deny_bash`; the two lists ARE the kernel's stated boundary for a
    node's Bash and its network and sub-agent tools."""

    on_auth_failure: FailureAction
    """What to do when the provider rejected the credential itself (``Escalation.on_auth_failure``).

    On the port because the decision is the COORDINATOR's, not the executor's: an executor
    can tell what went wrong, but only the coordinator knows whether this run is allowed to
    end resumably. Read by :meth:`Coordinator.work
    <agentdag.application.kernel.context.Coordinator.work>`'s body."""

    on_rate_limit: FailureAction
    """What to do when the provider refused the dispatch for quota (``Escalation.on_rate_limit``).

    Separate from :attr:`on_auth_failure` because the two want opposite answers and the
    provider's CLI reports them identically - only the credential probe separates them, and
    an operator must be able to set what happens for each."""

    run_limits: RunLimits
    """The whole run-limit block the tier policy declares, verbatim.

    The WHOLE object rather than the fields the dispatch path happens to read. Two of the
    nine used to be copied out here (``tokens_per_row`` and ``deadline_ceiling_s``) and the
    rest were unreachable, so a workflow program - which is handed the coordinator and
    nothing else - could not obtain the ``max_replans`` its own planning ladder binds on
    (:func:`~agentdag.application.kernel.root.run_root`), nor any node or depth bound. A
    port that carries part of a value it already holds is a port that has to be widened
    again for every new reader.

    Two of them bind on the dispatch path, and are read straight off here.
    ``run_limits.deadline_ceiling_s`` is the largest ``deadline_s`` any node may declare
    (design 2.3's run-limit clamp, rule 4): :meth:`Coordinator.work
    <agentdag.application.kernel.context.Coordinator.work>` clamps every dispatched node's
    ``spec.deadline_s`` to it before it reaches :class:`ExecutorRequest`, silently (no
    journal line), matching the same "out of scope, M2" note the other run-limit clamps in
    this codebase's own ``domain.policy`` module carry. ``run_limits.tokens_per_row`` is the
    ceiling the run-level budget check reads."""

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve ``spec``'s tier role (and any explicit model) to the row and executor to use."""
        ...


@dataclass(frozen=True, slots=True)
class ProbeFinding:
    """What a credential probe learned, and the raw observation it learned it from.

    The verdict alone loses the difference between "the provider said this credential is
    fine" and "the question could not be asked", and both arrive as ``INDETERMINATE``. That
    matters because an unmapped answer means the probe itself has gone wrong - a retired
    model id returning 404 is indistinguishable, by verdict, from a healthy timeout, and it
    silently restores the very defect the probe exists to fix.

    Attributes:
        verdict: What this says about the credential.
        detail: The observation behind it, in a few words an operator can act on - a status
            code, an unreachable endpoint, a missing token. Carried into the node's error
            message so it lands in ``record.json``, which is where this kernel puts things
            it wants seen; there is no logging in the kernel to put it in instead.
    """

    verdict: CredentialVerdict
    detail: str


class CredentialProbe(Protocol):
    """Asks the provider directly why it refused, when the executor's own report cannot say.

    The provider's CLI reports an exhausted quota and a rejected credential identically -
    same message, ``authentication_failed``, a null status field - so the difference has to
    come from somewhere else. This port is that somewhere: one call against the API with the
    same credential, read for the status code the CLI threw away.

    A port rather than a direct HTTP call so the executor stays testable without a network,
    and so an operator running against a provider that DOES discriminate can wire a probe
    that reads the discriminator instead of spending a request.
    """

    async def examine(self) -> ProbeFinding:
        """Report what the provider says about this credential right now, and how it said it.

        Must not raise: a probe that cannot reach the provider has learned nothing, which is
        :attr:`~agentdag.domain.models.CredentialVerdict.INDETERMINATE`, and a raise here
        would turn a failed diagnosis into a second, unrelated failure.
        """
        ...


class IsolationScanner(Protocol):
    """Takes a content manifest of a run's isolation root (design C8, the write-set net)."""

    def snapshot(self, root: Path) -> Mapping[str, str]:
        """Return relative POSIX path -> content hash for every file under ``root`` worth watching."""
        ...


class PathResolver(Protocol):
    """Resolves a path the way the filesystem will, symlinks followed.

    A port rather than a direct :func:`os.path.realpath` call because the rule that needs
    it - ``brief_ref`` containment (design 2.4) - is validation, and validation that reads
    the filesystem directly cannot be exercised without one. What must NOT happen is the
    caller resolving the path and handing in the result: the insertion review found the
    cwd containment guard already passes traversal that way, so the resolution stays on
    the validator's side of the boundary (decision 10, 2026-08-22).
    """

    def resolve(self, path: Path) -> Path:
        """Return ``path`` with every symlink and ``..`` resolved, whether or not it exists."""
        ...


class Scope(Protocol):
    """Starts, probes and kills the OS-level unit a node's executor runs under."""

    cross_process_capable: bool
    """Whether :meth:`is_alive`/:meth:`kill` give a TRUTHFUL answer for a
    :class:`ScopeHandle` RECONSTRUCTED in a different process than the one that started
    it (design 3.4, M3's ``run cancel``/startup sweep, both of which build a handle from
    a bare ``run_id`` in a fresh CLI invocation, never the coordinator's own process).
    ``True`` for a :class:`Scope` backed by OS-level, cross-process-queryable state (a
    systemd unit, its cgroup); ``False`` for one whose liveness lives only in this
    INSTANCE's own memory - such an implementation's :meth:`is_alive`/:meth:`kill` on an
    untracked handle report a harmless-looking default (``False``/``True``) that is
    correct for "this instance never started it" but WRONG, unverified, for "some OTHER
    process's coordinator might still be running it" - a caller checks this flag FIRST
    and never trusts either method's return for a cross-process handle when it is
    ``False``."""

    def start(self, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> ScopeHandle:
        """Start ``argv`` under a new scope named ``unit`` and return its handle.

        The launcher's own stdout/stderr are redirected to a ``launch.log`` file
        under ``cwd`` (append, owner-only), named on the returned handle as
        :attr:`ScopeHandle.log_path` - a coordinator traceback in a background
        launch would otherwise vanish with no terminal to print it to.
        """
        ...

    def confirm(self, handle: ScopeHandle, *, timeout_s: float) -> LaunchResult:
        """Wait up to ``timeout_s`` for the launch :meth:`start` began to prove itself.

        A caller MUST call this straight after :meth:`start`, before reporting the
        launch a success: ``start`` itself only Popens the launcher and returns, so
        without this a launcher that failed immediately (a bad unit name, a missing
        ``systemd-run``) would be reported ``started`` and exit 0 regardless.

        Returns:
            A result that is ``alive`` when the process is still running once
            ``timeout_s`` elapses, OR it exited cleanly (return code 0) within the
            window - both count as a proved launch. When it exited non-zero within
            the window, ``alive`` is ``False`` and ``stderr`` carries what
            :meth:`start` captured to ``launch.log``.
        """
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
    log_path: Path
    """Where the launcher's stdout/stderr were redirected (design Task 17 fix round 1)."""


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Whether a background launch proved itself within :meth:`Scope.confirm`'s timeout."""

    alive: bool
    """``True`` once the launch is proved: still running, or exited with code 0."""

    stderr: str
    """What :meth:`Scope.start` captured to ``launch.log``, when ``alive`` is ``False``."""


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

    Carries no ``runs_dir``: the CLI resolves that itself (``--runs`` or config
    ``kernel.runs_dir``) BEFORE calling ``wire_kernel``, needs it to open the run
    directory before any wiring exists, and passes it straight to
    :func:`~agentdag.application.kernel.run.run_coordinator` via ``run_dir`` - a
    second copy here would be redundant and could read differently from the one
    actually used.
    """

    journal_factory: Callable[[Path, Path], Journal]
    lock: RunLock
    clock: Clock
    executors: Mapping[str, Executor]
    gate_port: GatePort
    git: GitPort
    scanner: IsolationScanner
    policy: Policy
    registry: OpRegistry
    """Every op a plan may name, built by the composition root.

    On the wiring rather than assembled where it is used, because
    :func:`~agentdag.composition.kernel.build_op_registry` is the only thing that builds
    one and the layer contract forbids ``application`` importing ``composition``. It
    reaches a workflow program through the coordinator, which is all a program is handed."""

    scope: Scope
    sandbox: Sandbox
    notifier: Notifier
    parallel: int
