"""Dispatch: the ONE path every node takes (design 3.2).

Every primitive - work, gate, scan, reduce, map, stage, approve, apply - reaches the
world through :meth:`Dispatcher.dispatch`, so replay is served in exactly one place, the
crash window is re-run in exactly one place, and the sequence of keys a run dispatches
is recorded in exactly one place. That sequence is the replay-purity oracle: a rerun
that serves everything from the journal must produce the same keys, in the same order,
in the same number, and dispatch nothing.

The kernel never reads the wall clock directly; every timestamp and every duration comes
from the injected :class:`~agentdag.application.kernel.ports.Clock` (design 3.3, O19), so
a run's records are reproducible under a fake clock.

Contents:
    * :data:`Body` - a node's body, handed its node directory.
    * :class:`Dispatcher` - serves a key from the journal, or runs the body and records it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ...domain.journal import ResultLine, StartedLine
from ...domain.kernel_errors import KernelError
from ...domain.keys import canonical_json, content_hash, hash8, journal_key, prefix_hash
from ...domain.models import ErrorType, NodeError, NodeOutcome, NodeStatus, ResultRecord, SandboxGuarantees
from ...domain.scrub import scrub
from .ports import format_stamp, stamp
from .replay import build_replay_index

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ...domain.models import NodeSpec
    from .ports import Clock, Journal, RunDir
    from .replay import ReplayIndex

__all__ = ["Body", "Dispatcher"]

Body = Callable[[Path], Awaitable[NodeOutcome]]
"""A node's body: given the node's own directory, it produces the node's outcome.

A body is whatever the primitive does - run an executor, run a gate, fold a map's
branches - and it is the only part of a dispatch that talks to the outside world.
"""


@dataclass(frozen=True, slots=True)
class _Call:
    """The content-addressed identity of one dispatch, with the texts it was computed from."""

    key: str
    brief: str
    input_text: str
    input_hash: str


@dataclass
class Dispatcher:
    """Serves a node's result from the journal, or runs its body once and records it.

    The journal key carries no node id (design 3.2's identity table), so two nodes whose
    work is identical - same spec identity, same brief, same input, same dependency
    prefix - share one key: the second is SERVED the first's record, body unrun, and that
    record's ``node_id`` names the FIRST node. That is deliberate dedup (a map over a
    fleet that lists the same item twice is the legitimate case), not a collision.

    Attributes:
        journal: Where a dispatch's ``started`` and ``result`` lines are appended.
        run_dir: The run directory a node's brief, input and record are written under.
        clock: The one seam wall-clock time is read through.
        index: What the journal proved happened BEFORE this run - built once, at
            construction, so a key dispatched twice within one run really is
            dispatched twice (each attempt is its own journal line).
        records: Node id -> the latest record for it, filled as the program runs; the
            dependency lookup a journal key's prefix is computed from.
        dispatched_keys: This run's dispatch key sequence, served keys included - the
            replay-purity oracle.
    """

    journal: Journal
    run_dir: RunDir
    clock: Clock
    index: ReplayIndex
    # The parameterised factories (``dict[str, ResultRecord]`` rather than ``dict``) are what
    # keeps these fields fully typed under pyright strict, which infers a bare factory as Unknown.
    records: dict[str, ResultRecord] = field(default_factory=dict[str, ResultRecord])
    dispatched_keys: list[str] = field(default_factory=list[str])

    def reload_decisions(self) -> None:
        """Re-fold :attr:`journal`'s lines and refresh ONLY :attr:`index`'s decisions.

        Called by :meth:`~agentdag.application.kernel.context.Coordinator.fold_decisions`
        right after appending a fresh ``approve_decision`` line, so that decision is
        visible to the next ``approve`` call THIS run makes. Replaces only
        ``index.decisions`` - ``results``, ``crash_window`` and ``key_sequence`` stay
        exactly as :meth:`from_journal` built them at construction. Every dispatch
        this run makes already goes through this one dispatcher (the module
        docstring's "ONE path"), so nothing else in the journal can have changed
        since construction; re-deriving those three fields from scratch here would
        silently paper over that invariant if it were ever violated, instead of
        leaving them untouched so a violation stays visible.
        """
        self.index.decisions = build_replay_index(self.journal.lines()).decisions

    def reload_grants(self) -> None:
        """Re-fold :attr:`journal`'s lines and refresh ONLY :attr:`index`'s retry grants.

        Called by :meth:`~agentdag.application.kernel.context.Coordinator.fold_retry_grants`
        right after appending fresh ``retry_grant`` lines. The index was built at
        construction, BEFORE those lines existed, so without this the grants an operator
        recorded while no coordinator ran would be invisible to the launch that folded them.

        Replaces only ``index.grants``, for the reason :meth:`reload_decisions` states about
        its own field: nothing else in the journal can have changed since construction, and
        re-deriving the other fields here would paper over a violation of that rather than
        leaving it visible.
        """
        self.index.grants = build_replay_index(self.journal.lines()).grants

    @classmethod
    def from_journal(cls, *, journal: Journal, run_dir: RunDir, clock: Clock) -> Dispatcher:
        """Build a dispatcher whose replay index is folded from ``journal``'s current lines.

        Args:
            journal: The run's journal; its lines are read once, here.
            run_dir: The run directory.
            clock: The clock every timestamp and duration is read from.

        Returns:
            A dispatcher ready to serve everything the journal already proved.
        """
        return cls(journal=journal, run_dir=run_dir, clock=clock, index=build_replay_index(journal.lines()))

    async def dispatch(
        self,
        spec: NodeSpec,
        *,
        brief: str,
        input_obj: Mapping[str, Any],
        body: Body,
        sandbox: SandboxGuarantees | None = None,
    ) -> ResultRecord:
        """Serve this call's record from the journal, or run ``body`` once and record it.

        The key is appended to :attr:`dispatched_keys` BEFORE the index is consulted, so
        the sequence is comparable with the journal's ``started`` keys whether a call was
        served or run.

        Args:
            spec: The node being dispatched; its identity fields, its dependencies'
                records and the two texts below make the key.
            brief: The node's brief, written to ``brief.md`` in its node directory.
            input_obj: The assembled input, written to ``input.json`` as canonical JSON.
            body: What to run when the journal has no result for this key.
            sandbox: What isolation boundary this call runs under (Task 19), or ``None``
                when the caller has none to declare. Stamped onto the record at
                CONSTRUCTION time, before ``record.json`` is written and the journal's
                ``result`` line is appended, so it is what actually gets persisted - not
                applied here at all when ``call.key`` is already served: a served record
                keeps whatever declaration it was ORIGINALLY dispatched under, because it
                is read back from the journal untouched, never rebuilt.

        Returns:
            The record for this call: the journaled one when it was served, otherwise
            the one this dispatch just wrote.

        Raises:
            KernelError: a dependency named in ``spec.deps`` has no record yet - the
                program dispatched a node before the node it depends on.
        """
        call = self._identify(spec, brief=brief, input_obj=input_obj)
        self.dispatched_keys.append(call.key)
        served = self.index.results.get(call.key)
        if served is not None:
            self.records[spec.node_id] = served
            return served  # replay: no node dir, no started line, no body, no re-stamp
        return await self._run_and_record(spec, call, body, sandbox=sandbox)

    def _identify(self, spec: NodeSpec, *, brief: str, input_obj: Mapping[str, Any]) -> _Call:
        """Compute this call's journal key from the spec, its dependencies' records and its texts."""
        input_text = canonical_json(dict(input_obj))
        input_hash = content_hash(input_text)
        prefix = prefix_hash([self._dep_record(spec.node_id, dep) for dep in spec.deps])
        key = journal_key(spec, brief_hash=content_hash(brief), input_hash=input_hash, prefix=prefix)
        return _Call(key=key, brief=brief, input_text=input_text, input_hash=input_hash)

    def _dep_record(self, node_id: str, dep: str) -> ResultRecord:
        """Look up ``dep``'s record, or refuse with a typed error naming both nodes.

        Raises:
            KernelError: ``dep`` has no record yet - a program bug, not something a
                retry fixes: fix the dispatch order and re-run.
        """
        try:
            return self.records[dep]
        except KeyError as exc:
            raise KernelError(f"node {node_id!r} depends on {dep!r}, which this run has not dispatched") from exc

    async def _run_and_record(
        self, spec: NodeSpec, call: _Call, body: Body, *, sandbox: SandboxGuarantees | None
    ) -> ResultRecord:
        """Run one dispatch for real: inputs, ``started``, the body, the record, ``result``.

        A crash-window re-run computes this exact same key (design: the crash happens
        AFTER the ``started`` line, before ``result``), so it reaches the SAME
        ``node_dir`` (``hash8`` of the key) as the interrupted attempt. ``brief.md`` and
        ``input.json`` are simply overwritten here, but anything ``body`` itself wrote
        into ``node_dir`` on the crashed attempt is still there when it runs again -
        node-local idempotency is ``body``'s job, not this method's.
        """
        node_dir = self.run_dir.node_dir(spec.node_id, hash8(call.key))
        self._write(node_dir, "brief.md", call.brief)
        self._write(node_dir, "input.json", call.input_text)
        started = self.clock.now()
        self.journal.append(
            StartedLine(key=call.key, node_id=spec.node_id, attempt=spec.attempt, at=format_stamp(started))
        )
        outcome = _refuse_empty(await _run_body(body, node_dir))
        duration_s = (self.clock.now() - started).total_seconds()
        # ResultRecord.input_hash is the record's OWN journal key (result-record.schema.json's
        # field description, and its examples), not call.input_hash - that is only ONE
        # ingredient journal_key() hashed together with brief_hash and prefix to produce it.
        record = _complete(outcome, spec=spec, input_hash=call.key, duration_s=duration_s, sandbox=sandbox)
        self._write(node_dir, "record.json", record.model_dump_json(by_alias=True, indent=1))
        self.journal.append(ResultLine(key=call.key, record=record, at=stamp(self.clock)))
        self.records[spec.node_id] = record
        return record

    def _write(self, node_dir: Path, name: str, text: str) -> None:
        """Write ``name`` into ``node_dir`` through the run dir's atomic writer.

        The port takes a POSIX-style path relative to the run root, so the relative
        path is rendered with :meth:`~pathlib.PurePath.as_posix` rather than ``str``,
        which would hand it backslashes on Windows.
        """
        self.run_dir.write_atomic(f"{node_dir.relative_to(self.run_dir.root).as_posix()}/{name}", text)


async def _run_body(body: Body, node_dir: Path) -> NodeOutcome:
    """Run a node's body, turning any raise into a failed outcome rather than a dead run.

    ``Exception`` only: ``SystemExit`` and ``KeyboardInterrupt`` are the coordinator
    process itself going away, and must stay a crash - a crash leaves a ``started`` line
    with no ``result``, which is exactly what the next run re-dispatches.

    A :class:`~agentdag.domain.kernel_errors.KernelError` is stamped
    ``transient=False`` and anything else ``transient=True``: the kernel raises that
    family for a CONFIGURATION or PROGRAM bug (an effort the policy does not name, a
    cwd outside the isolation root, a dependency dispatched out of order), which the
    same inputs reproduce every time, so retrying it only burns the budget. Every
    other exception comes from the outside world the body was talking to, where a
    retry is the reasonable default.

    The exception's own text is scrubbed (:func:`~agentdag.domain.scrub.scrub`) before
    it becomes ``NodeError.message``: a raising body can carry a secret-shaped string
    in its exception text just as readily as a streamed executor message can (an HTTP
    client's own error sometimes echoes a header back), and ``record.json`` is the
    same sink :mod:`agentdag.adapters.kernel.executor_claude` already scrubs before
    writing to.
    """
    try:
        return await body(node_dir)
    except KernelError as exc:  # a config or program bug: the same inputs fail the same way
        return _failed_outcome(exc, transient=False)
    except Exception as exc:  # a raising branch is a FAILED RECORD, never a dead fleet
        return _failed_outcome(exc, transient=True)


def _failed_outcome(exc: Exception, *, transient: bool) -> NodeOutcome:
    """Build the failed outcome a raising body is recorded as, with its text scrubbed.

    Args:
        exc: What the body raised.
        transient: Whether a retry could plausibly succeed - see :func:`_run_body`.

    Returns:
        A failed :class:`~agentdag.domain.models.NodeOutcome` naming the exception type.
    """
    return NodeOutcome(
        status=NodeStatus.FAILED,
        executor_used="-",
        model_used="-",
        effort_used="-",
        error=NodeError(
            type=ErrorType.EXECUTOR_ERROR,
            message=cast("str", scrub(f"{type(exc).__name__}: {exc}")),
            transient=transient,
        ),
    )


def _refuse_empty(outcome: NodeOutcome) -> NodeOutcome:
    """Refuse a done outcome that reported nothing (design 9, "empty result counted").

    A node that claims success with no artefact reference AND no key fact named in
    ``typed_fields`` has produced nothing the coordinator can branch on, so it is a
    failure with a named error type, not a silent success.

    Args:
        outcome: What the body returned.

    Returns:
        ``outcome`` unchanged, or a failed copy carrying ``agents_empty_result``.

    Example:
        >>> from agentdag.domain.models import NodeOutcome, NodeStatus
        >>> empty = NodeOutcome(status=NodeStatus.DONE, executor_used="code", model_used="-", effort_used="-")
        >>> _refuse_empty(empty).status.value
        'failed'
        >>> _refuse_empty(empty.model_copy(update={"artefact_refs": ["tally.json"]})).status.value
        'done'
    """
    if outcome.status is not NodeStatus.DONE or outcome.artefact_refs:
        return outcome
    if any(name in outcome.key_facts for name in outcome.typed_fields):
        return outcome
    return outcome.model_copy(
        update={
            "status": NodeStatus.FAILED,
            "error": NodeError(
                type=ErrorType.AGENTS_EMPTY_RESULT,
                message="no artefact refs and no typed key_facts",
                transient=False,
            ),
        }
    )


def _complete(
    outcome: NodeOutcome, *, spec: NodeSpec, input_hash: str, duration_s: float, sandbox: SandboxGuarantees | None
) -> ResultRecord:
    """Complete a body's outcome into the record the coordinator branches on (design 2.2).

    Args:
        outcome: What the body returned, after the empty-result refusal.
        spec: The dispatched spec, for the node id and attempt the record carries.
        input_hash: The journal key this call was dispatched under (``call.key``) -
            what ``result-record.schema.json`` names ``input_hash`` and documents as
            "the journal key this record was dispatched under", not the plain content
            hash of ``input.json`` alone (that hash is only one ingredient
            :func:`~agentdag.domain.keys.journal_key` combines with the brief hash and
            the dependency prefix to produce the key).
        duration_s: How long the body ran, measured on the injected clock.
        sandbox: What isolation boundary this dispatch ran under (Task 19), or ``None``
            when the caller declared none. ``sandbox`` lives on
            :class:`~agentdag.domain.models.ResultRecord`, not on ``outcome`` (a
            :class:`~agentdag.domain.models.NodeOutcome`), so it is added as an explicit
            key here rather than coming from ``outcome.model_dump()`` - the same reason
            ``node_id``, ``attempt``, ``input_hash`` and ``duration_s`` are.

    Returns:
        The full :class:`~agentdag.domain.models.ResultRecord`.
    """
    return ResultRecord.model_validate(
        {
            **outcome.model_dump(),
            "node_id": spec.node_id,
            "attempt": spec.attempt,
            "input_hash": input_hash,
            "duration_s": duration_s,
            "sandbox": sandbox,
        }
    )
