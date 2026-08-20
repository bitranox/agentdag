"""The coordinator context: what a workflow program is handed (design 3.2, section 4).

A workflow program is deterministic code over typed records. It never touches the world
directly: every effect it has goes through one of the primitives on :class:`Coordinator`,
and every primitive goes through :meth:`~agentdag.application.kernel.dispatch.Dispatcher.dispatch`,
so replay, the crash window, the journal key and the spend accounting are each defined in
exactly one place.

Contents:
    * :class:`Coordinator` - the primitives a workflow program calls, and the run-scoped
      state (tokens per model row) a run summary is written from.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from pydantic import BaseModel

from ...domain.journal import ApproveDecisionLine
from ...domain.kernel_errors import KernelError, SpecRejected, Suspended
from ...domain.keys import canonical_json, content_hash, hash8
from ...domain.models import Decision, ErrorType, NodeError, NodeOutcome, NodeStatus, ResultRecord
from ...domain.scan import diff_manifests, stray_paths
from .ports import ExecutorRequest, stamp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from pathlib import Path
    from typing import Any

    from ...domain.models import ApprovePayload, NodeSpec
    from ..graph_a_ports import GatePort, GitPort
    from .dispatch import Body, Dispatcher
    from .ports import Clock, Executor, IsolationScanner, Policy, RunDir
    from .sandbox import Sandbox

__all__ = ["BranchRef", "Coordinator", "HasDedupKey"]

_ItemT = TypeVar("_ItemT")


class HasDedupKey(Protocol):
    """What makes performing an intent idempotent: the one field ``stage``/``apply`` read (design 3.4).

    Every real intent (e.g. :class:`~agentdag.domain.graph_a.PushIntent`) is a pydantic
    model that already has this field; the protocol exists only so ``intent.dedup_key``
    type-checks under pyright strict without widening :meth:`Coordinator.stage` or
    :meth:`Coordinator.apply` to know about any one workflow's concrete intent type.
    """

    dedup_key: str


@dataclass(frozen=True, slots=True)
class BranchRef:
    """One map branch's identity in a written manifest (``manifest/<map_id>.json``, design 3.1).

    Built by the workflow's ``fold`` (the code a :meth:`Coordinator.reduce` call runs) from
    the records :meth:`Coordinator.map` returned, then handed to :meth:`Coordinator.write_manifest`.
    """

    index: int
    node_id: str
    key: str
    status: str


class Coordinator:
    """What a workflow program is handed: every effect goes through a primitive here.

    Attributes:
        run_id: This run's id, as minted by the scheduler.
        workflow: The workflow program's name.
        args: The program's typed arguments (a model, or the mapping a test passes).
        dispatcher: The one path a node takes; also the run's key sequence and records.
        run_dir: The run directory every artefact and every node directory sits under.
        clock: The one seam a workflow reads time through (``co.clock.now()``).
        executors: Executor name -> executor, as the policy's rows name them.
        gate_port: The mechanical gate a ``gate`` node runs.
        git: Every git operation a workflow performs.
        scanner: Takes the isolation-root manifest a ``scan`` node compares.
        policy: Resolves a spec to a model row, and carries the executor limits.
        sandbox: What isolation boundary every dispatched node runs under (Task 19); its
            :meth:`~.sandbox.Sandbox.guarantees` is read once per dispatch by
            :meth:`_dispatch`, whatever primitive dispatched it, and handed to the
            dispatcher, which stamps it onto the record it builds.
        parallel: How many map branches may run at once, across the WHOLE run - the
            semaphore behind it is built once here and shared by every :meth:`map`
            call, so two maps running at the same time still admit ``parallel``
            branches between them, not ``parallel`` each.
        tokens_by_row: Tokens charged per model row so far, summed from every record.
        declared_write_sets: Every dispatched spec's node id -> its ``write_set``, as
            given at dispatch time (design C8) - what :meth:`scan` judges a write
            against, so it never mistakes another node's own declared writes (a
            sibling map branch, or the watched node's own dispatcher bookkeeping
            under ``nodes/<node_id>/**``) for a stray one.
    """

    DEFAULT_PROMPT = (
        "Apply the change described in your system prompt to this repository. Commit with a clear message. Do not push."
    )
    """The prompt a work node runs under when the workflow does not name its own."""

    # Thirteen keyword-only parameters because the coordinator IS its wiring: every seam a
    # workflow can reach is injected here, and bundling them into a record would only
    # rename the same list. PLR0913 is off for this package by pyproject's per-file-ignores.
    def __init__(
        self,
        *,
        run_id: str,
        workflow: str,
        args: BaseModel | Mapping[str, object],
        dispatcher: Dispatcher,
        run_dir: RunDir,
        clock: Clock,
        executors: Mapping[str, Executor],
        gate_port: GatePort,
        git: GitPort,
        scanner: IsolationScanner,
        policy: Policy,
        sandbox: Sandbox,
        parallel: int,
    ) -> None:
        """Bind one run's wiring; ``tokens_by_row`` starts empty."""
        self.run_id = run_id
        self.workflow = workflow
        self.args = args
        self.dispatcher = dispatcher
        self.run_dir = run_dir
        self.clock = clock
        self.executors = executors
        self.gate_port = gate_port
        self.git = git
        self.scanner = scanner
        self.policy = policy
        self.sandbox = sandbox
        self.parallel = parallel
        # Run-wide, not per-map: `parallel` is what this HOST may run at once (worktrees,
        # the shared bmk tool env, the executor's own concurrency), so a second concurrent
        # map must not double it. asyncio.Semaphore binds to the running loop lazily, on
        # the first `async with`, so building it here - outside the loop - is fine.
        self._map_semaphore = asyncio.Semaphore(parallel)
        self.tokens_by_row: dict[str, int] = {}
        self.declared_write_sets: dict[str, tuple[str, ...]] = {}

    async def work(self, spec: NodeSpec, *, brief: str, cwd: Path, prompt: str = DEFAULT_PROMPT) -> ResultRecord:
        """Dispatch one work node: an executor, running ``brief`` against ``cwd``.

        The policy resolves the spec to a model row before the key is computed, and the
        resolved row and executor are written back onto the dispatched spec - so a
        policy change that moves this spec's RESOLVED ROW (its model alias or its
        executor) is a different call, not a silently different node. ``max_turns`` and
        ``deny_bash`` reach the executor request but are not part of the key or of
        ``input_obj``, so changing only those does not.

        Args:
            spec: The node spec, with its tier role, write set, deps and limits.
            brief: The node's brief; its content hash is part of the journal key.
            cwd: The working directory the executor runs in; recorded in the key as a
                path relative to the run root, so a run directory that moves is still
                the same call.
            prompt: What the executor is told to do with the brief.

        Returns:
            The node's result record, with this node's charged tokens already added to
            :attr:`tokens_by_row`.

        Raises:
            KernelError: the resolved row names an executor not in :attr:`executors`,
                or ``cwd`` sits outside the run root - both a misconfiguration, so they
                raise HERE, before anything is dispatched: a failed record for either
                would just be retried forever, never fixed by another attempt.
        """
        row = self.policy.resolve(spec)
        if row.executor not in self.executors:
            raise KernelError(
                f"executor {row.executor!r} for node {spec.node_id!r} is not wired; wired: {sorted(self.executors)}"
            )
        executor = self.executors[row.executor]
        try:
            cwd_rel = cwd.relative_to(self.run_dir.root)
        except ValueError as exc:
            raise KernelError(
                f"cwd {cwd} of node {spec.node_id!r} is outside the run root {self.run_dir.root}"
            ) from exc
        input_obj = {
            "cwd": cwd_rel.as_posix(),
            "prompt": prompt,
            "model": row.alias,
            "effort": spec.effort,
        }

        async def body(node_dir: Path) -> NodeOutcome:
            request = ExecutorRequest(
                node_dir=node_dir,
                cwd=cwd,
                brief=brief,
                prompt=prompt,
                model=row.alias,
                effort=spec.effort,
                max_turns=self.policy.max_turns,
                isolation_root=self.run_dir.root,
                write_set=tuple(spec.write_set),
                deny_bash=self.policy.deny_bash,
            )
            return await executor.run(request)

        dispatched = spec.model_copy(update={"executor": row.executor, "model": row.alias})
        return await self._dispatch(dispatched, brief=brief, input_obj=input_obj, body=body)

    def snapshot(self) -> Mapping[str, str]:
        """Take the isolation-root manifest a later :meth:`scan` compares against.

        Returns:
            Relative POSIX path -> content hash, for everything under the run root the
            scanner watches. Taken BEFORE the node whose writes it will judge.
        """
        return self.scanner.snapshot(self.run_dir.root)

    async def gate(self, spec: NodeSpec, *, argv: Sequence[str], cwd: Path) -> ResultRecord:
        """Dispatch a mechanical gate: run ``argv`` in ``cwd`` and record its exit code.

        ``argv`` is what a workflow calls this gate for - the log line, the record's key -
        but the command actually run is whatever :attr:`gate_port` was built with; a
        different ``argv`` still makes a different journal key even if the port's own
        command did not change, because ``argv`` is part of ``input_obj``.

        Args:
            spec: The gate node's spec.
            argv: The gate command, recorded for the key and the brief; not itself what
                :attr:`gate_port` runs (the port carries its own fixed command).
            cwd: The working directory the gate runs in; recorded in the key as a path
                relative to the run root, like :meth:`work`'s ``cwd``.

        Returns:
            The gate's record: ``done`` on exit code 0, else ``failed``, with the exit
            code in ``key_facts["rc"]`` and the gate's combined output at
            ``artefact_refs[0]``.

        Raises:
            KernelError: ``cwd`` sits outside the run root.
        """
        try:
            cwd_rel = cwd.relative_to(self.run_dir.root).as_posix()
        except ValueError as exc:
            raise KernelError(
                f"cwd {cwd} of node {spec.node_id!r} is outside the run root {self.run_dir.root}"
            ) from exc
        input_obj = {"argv": list(argv), "cwd": cwd_rel}

        async def body(node_dir: Path) -> NodeOutcome:
            log = node_dir / "gate.log"
            rc = self.gate_port.run(cwd, log)
            rel_log = f"{node_dir.relative_to(self.run_dir.root).as_posix()}/gate.log"
            # A red gate is an ordinary FAILED outcome, not an executor error - the mechanical
            # step ran to completion and reported a real answer, so `error` stays unset. The
            # result-record schema (schemas/result-record.schema.json) does not list `error`
            # under `required`, so `failed` with no `error` is schema-valid; `rc` in `key_facts`
            # is what a workflow branches on.
            return NodeOutcome(
                status=NodeStatus.DONE if rc == 0 else NodeStatus.FAILED,
                artefact_refs=[rel_log],
                key_facts={"rc": rc},
                typed_fields=["rc"],
                executor_used="code",
                model_used="-",
                effort_used="-",
            )

        return await self._dispatch(spec, brief=f"gate: {' '.join(argv)}", input_obj=input_obj, body=body)

    async def scan(
        self, spec: NodeSpec, *, watched: str, before: Mapping[str, str], write_set: Sequence[str]
    ) -> ResultRecord:
        """Dispatch the isolation-root scan as a gate node: writes to an UNDECLARED path are the finding.

        A stray write is judged against every write set ANY spec in this run has
        declared so far (:attr:`declared_write_sets`, filled by :meth:`_dispatch` for
        every dispatched node - ``watched``'s own declared write set is already in
        there, so passing ``write_set`` again is redundant but harmless), plus the
        run-root's own housekeeping prefixes (``nodes/**``, ``manifest/**``,
        ``intents/**``, ``artefacts/**``, ``done/**``). This is deliberately NOT
        "``write_set`` plus the watched node's own dir": that older rule flagged the
        watched node's OWN bookkeeping (``nodes/<watched>/<hash8>/{brief.md,input.json,
        record.json,transcript.jsonl}``, written by the dispatcher/executor between
        ``before`` and ``after``) as a stray write, and under ``parallel > 1`` it also
        flagged a SIBLING branch's legitimate writes into its own declared worktree.

        Limit: under ``parallel > 1``, a stray write that lands INSIDE a sibling's
        declared region is not attributable by a content diff alone - the diff can see
        that something changed there, but not which of the several concurrently
        running nodes wrote it. That case is caught only at ``parallel=1``, or by the
        process-isolation boundary a later milestone adds. A write to a path NOBODY
        declared (a foreign worktree, ``$HOME`` inside the run root; ``/tmp`` is
        outside the run root entirely, so it is never even in the manifest) is caught
        either way, concurrency included.

        ``wt/.partial-*/**`` is allowed for the same reason as ``nodes/**``: it is the
        coordinator's OWN bookkeeping (graph A's staging clone, cleaned up and renamed
        by ``_ensure_worktree`` before its branch's own snapshot), not a node's write.
        Ordering closes the common case - a branch never sees its own staging dir
        appear or disappear - but under ``parallel > 1`` a SIBLING's snapshot can still
        land while this branch's staging dir briefly exists or is being removed; this
        exclusion is what keeps that residual window from reading as a stray write.

        Args:
            spec: The scan node's spec.
            watched: What this scan is watching, for the brief and the log; free text.
            before: The manifest :meth:`snapshot` took before the watched node ran.
            write_set: The globs the watched node was allowed to write to.

        Returns:
            ``done`` when nothing strayed (``key_facts["stray"] == []``), else ``failed``
            with the stray paths in ``key_facts["stray"]``.
        """
        other_declared = [
            pattern
            for node_id, patterns in self.declared_write_sets.items()
            if node_id != watched
            for pattern in patterns
        ]
        allowed = [
            *write_set,
            *other_declared,
            "nodes/**",
            "manifest/**",
            "intents/**",
            "artefacts/**",
            "done/**",
            "wt/.partial-*/**",  # a staging clone mid-rename: coordinator bookkeeping, not a node write
        ]
        input_obj = {"watched": watched, "write_set": list(write_set)}

        async def body(node_dir: Path) -> NodeOutcome:
            after = dict(self.scanner.snapshot(self.run_dir.root))
            stray = stray_paths(diff_manifests(dict(before), after), allowed=allowed)
            return NodeOutcome(
                status=NodeStatus.DONE if not stray else NodeStatus.FAILED,
                key_facts={"stray": stray},
                typed_fields=["stray"],
                executor_used="code",
                model_used="-",
                effort_used="-",
            )

        return await self._dispatch(spec, brief=f"scan: {watched}", input_obj=input_obj, body=body)

    async def reduce(
        self, spec: NodeSpec, *, fold: Callable[[], NodeOutcome], input_obj: Mapping[str, object] | None = None
    ) -> ResultRecord:
        """Dispatch a code fold: ``fold`` runs as the node's body and its outcome is the record.

        Args:
            spec: The reduce node's spec.
            fold: Builds this node's outcome; runs synchronously as the dispatch body,
                so whatever it raises becomes a ``failed`` record like any other body.
            input_obj: Extra identity fields a caller wants folded into this call's key
                (e.g. the content hash of a repos file a fleet was built from) on top of
                ``{"kind": "reduce"}``, which is always present.

        Returns:
            The record :meth:`~agentdag.application.kernel.dispatch.Dispatcher.dispatch`
            wrote for ``fold``'s outcome.
        """
        merged_input: dict[str, object] = {"kind": "reduce", **(dict(input_obj) if input_obj is not None else {})}

        async def body(node_dir: Path) -> NodeOutcome:
            return fold()

        return await self._dispatch(spec, brief=f"reduce: {spec.node_id}", input_obj=merged_input, body=body)

    def write_manifest(self, map_id: str, branches: Sequence[BranchRef]) -> Path:
        """Write ``manifest/<map_id>.json``: the map a reduce closes (design 3.1).

        Called by the workflow's ``fold`` (the callable passed to :meth:`reduce`) once
        it has judged every branch :meth:`map` returned - this method itself does not
        run inside a dispatch, so calling it more than once for the same ``map_id``
        simply REWRITES the manifest; it is not idempotent content-wise, because
        ``reduced_at`` is read from :attr:`clock` at THIS call, so a repeat call's
        manifest carries a different ``reduced_at`` than the first even when every
        branch is identical.

        Args:
            map_id: The map's id, as passed to :meth:`map`.
            branches: Each branch's index, node id, key and status, in branch order.

        Returns:
            The path written, as :meth:`~agentdag.application.kernel.ports.RunDir.write_atomic` returns it.
        """
        payload = {
            "map_id": map_id,
            "branches": [{"index": b.index, "node_id": b.node_id, "key": b.key, "status": b.status} for b in branches],
            "reduced_at": stamp(self.clock),
            "reducer_version": "1",
        }
        target = self.run_dir.manifest_path(map_id)
        rel = target.relative_to(self.run_dir.root).as_posix()
        return self.run_dir.write_atomic(rel, canonical_json(payload))

    async def map(
        self, map_id: str, items: Sequence[_ItemT], body: Callable[[int, _ItemT], Awaitable[ResultRecord]]
    ) -> list[ResultRecord]:
        """Fan out over ``items``; one raising branch never kills the run.

        The concurrency limit is the coordinator's ONE run-wide semaphore, not a fresh
        per-map one: at most :attr:`parallel` branches run at once counting every map
        this run has in flight, so a workflow that fans out twice concurrently still
        holds the host to the limit the operator asked for.

        Each branch's own dispatch (inside ``body``) still goes through :meth:`_dispatch`,
        so a raising branch never bypasses charging - only a branch that raises OUTSIDE any
        dispatch call (a clone that blew up before it ever reached ``work``/``gate``/...)
        gets the synthetic record built here, and that one is NEVER journaled: it names no
        real dispatch, so there is nothing for a later replay to serve back.

        Args:
            map_id: This map's id; a raising branch's synthetic node id is ``f"{map_id}@{i}"``.
            items: One item per branch, in the order results are returned.
            body: Runs one branch; whatever it dispatches is charged and journaled normally.

        Returns:
            One record per item, in item order - a raising branch's record included.

        Raises:
            BaseException: a branch raised something that is not an ``Exception`` (a
                ``SystemExit``, a ``KeyboardInterrupt``, an ``asyncio.CancelledError``) -
                the coordinator process itself going away, exactly like
                :func:`~agentdag.application.kernel.dispatch._run_body`'s own rule, so it
                is never swallowed into a synthetic record.
        """

        async def bounded(index: int, item: _ItemT) -> ResultRecord:
            async with self._map_semaphore:
                return await body(index, item)

        outcomes = await asyncio.gather(*(bounded(i, item) for i, item in enumerate(items)), return_exceptions=True)
        return [_branch_record(map_id, index, outcome) for index, outcome in enumerate(outcomes)]

    async def stage(self, spec: NodeSpec, *, intents: Sequence[HasDedupKey], kind: str) -> ResultRecord:
        """Write every intent under ``intents/<kind>/`` BEFORE anything leaves the process (design 3.4).

        Args:
            spec: The stage node's spec.
            intents: What to stage; each intent's :attr:`~HasDedupKey.dedup_key` names
                its file, ``intents/<kind>/<dedup_key>.json``.
            kind: The intent kind - the subdirectory every intent of this stage lands in.

        Returns:
            ``done``, with ``key_facts`` carrying the staged count and every dedup key.
        """
        keys = [intent.dedup_key for intent in intents]
        input_obj = {"kind": kind, "keys": keys}

        async def body(node_dir: Path) -> NodeOutcome:
            refs = [self._write_intent(kind, intent) for intent in intents]
            return NodeOutcome(
                status=NodeStatus.DONE,
                artefact_refs=refs,
                key_facts={"count": len(intents), "keys": keys},
                typed_fields=["count", "keys"],
                executor_used="code",
                model_used="-",
                effort_used="-",
            )

        return await self._dispatch(spec, brief=f"stage: {kind}", input_obj=input_obj, body=body)

    def _write_intent(self, kind: str, intent: HasDedupKey) -> str:
        """Write one intent's JSON to ``intents/<kind>/<dedup_key>.json``; return the path, run-relative.

        Raises:
            KernelError: ``intent`` is not a pydantic model - every real intent this
                kernel stages is one, and only a model gives a stable JSON rendering.
        """
        if not isinstance(intent, BaseModel):
            raise KernelError(f"intent for kind {kind!r} is not a pydantic model: {type(intent)!r}")
        rel = f"intents/{kind}/{intent.dedup_key}.json"
        self.run_dir.write_atomic(rel, intent.model_dump_json(indent=1))
        return rel

    async def approve(self, spec: NodeSpec, *, payload: ApprovePayload) -> Decision:
        """Return the decision recorded for THIS payload, or write the payload and suspend the run.

        Never blocks and never polls: with no decision recorded for the payload on
        offer, this writes the payload and RAISES - the coordinator process exits, and a
        later relaunch that has folded a matching decision (:meth:`fold_decisions`) is
        what makes this call return.

        The lookup is by (node id, payload hash), never by node id alone, and that IS
        design 3.4's binding (the idempotency key D2 took from DBOS's
        ``send(..., idempotency_key)``): a decision answers one exact payload. When the
        payload CHANGES between the suspend and the resume - M3's retry turning a failed
        repo into a passed one, a worktree edited by hand - the old decision simply does
        not match, so the run writes the NEW payload and suspends again on it rather
        than applying an approval for a list nobody was shown. Nothing is refused and
        nothing has to be deleted by hand: the decider answers the new payload, and both
        answers stay on disk under their own hashes.

        A decision, once recorded, is FINAL for that (node id, payload hash):
        :meth:`~agentdag.application.kernel.ports.RunDir.write_decision` refuses a
        second write for the SAME pair, a ``hold`` included, so there is no
        revise-the-last-verdict path. A ``hold`` therefore stands until the WORLD
        changes - a different payload gets a fresh suspend, never a second vote on the
        one already answered.

        Two different ``payload.json`` locations are intentional, one per path below.
        The SUSPEND path writes to ``nodes/<node_id>/<hash8(payload content
        hash)>/payload.json`` BEFORE any dispatch happens - there IS no dispatch
        node_dir yet at that point (the coordinator is about to raise and exit), so
        this is the only stable, content-addressed place to put it, and it is where the
        decider reads the payload they are answering. An existing file there is left
        alone: the same payload hashes to the same directory, so rewriting it could only
        ever reproduce the same bytes. Once a decision exists, the DONE path instead
        writes ``payload.json`` INSIDE the dispatch's own body, into the record's REAL
        node_dir (``hash8`` of the call's journal key, computed by
        :class:`~agentdag.application.kernel.dispatch.Dispatcher` itself) - so
        ``artefact_refs`` names a file that actually exists under the SAME hash as the
        record that references it, rather than the differently-hashed directory the
        suspend path used (a payload-content hash, not a journal-key hash - the two
        hashes agree only by coincidence).

        Args:
            spec: The approve node's spec.
            payload: What to show the human, including the option to fall back on.

        Returns:
            The recorded :class:`~agentdag.domain.models.Decision`, once one exists,
            carrying the payload hash it was applied to.

        Raises:
            SpecRejected: ``payload.default`` does not name an option whose
                ``effect == "none"`` - a default the coordinator could apply unattended
                must never itself leave the process (design 2.4).
            Suspended: no decision is recorded yet for this (node id, payload hash);
                the exception carries the hash, so a caller knows WHICH payload to ask about.
        """
        _validate_default(payload)
        payload_text = payload.model_dump_json(indent=1)
        payload_hash = content_hash(payload_text)

        line = self._folded_decision(spec.node_id, payload_hash)
        if line is None:
            self._write_suspend_payload(spec.node_id, payload_hash, payload_text)
            raise Suspended(spec.node_id, payload_hash=payload_hash)

        async def body(node_dir: Path) -> NodeOutcome:
            rel_payload = f"{node_dir.relative_to(self.run_dir.root).as_posix()}/payload.json"
            self.run_dir.write_atomic(rel_payload, payload_text)
            return NodeOutcome(
                status=NodeStatus.DONE,
                artefact_refs=[rel_payload],
                key_facts={"decision": line.decision},
                typed_fields=["decision"],
                executor_used="code",
                model_used="-",
                effort_used="-",
            )

        await self._dispatch(
            spec, brief=f"approve: {spec.node_id}", input_obj={"payload_hash": payload_hash}, body=body
        )
        return Decision(
            node_id=line.node_id,
            decision=line.decision,
            reason=line.reason,
            by=line.by,
            token_id=line.token_id,
            payload_hash=payload_hash,
        )

    def _folded_decision(self, node_id: str, payload_hash: str) -> ApproveDecisionLine | None:
        """Return the decision folded for exactly this (node, payload), or ``None``.

        Args:
            node_id: The approve node being asked about.
            payload_hash: The content hash of the payload on offer right now.

        Returns:
            The decision line recorded for this exact pair. ``None`` when no such
            decision has been folded yet - a CHANGED payload is a new question with no
            answer of its own, never the old payload's answer carried over.
        """
        return self.dispatcher.index.decisions.get((node_id, payload_hash))

    def _write_suspend_payload(self, node_id: str, payload_hash: str, payload_text: str) -> None:
        """Publish the payload the decider is being asked about, unless it is already there."""
        suspend_dir = self.run_dir.node_dir(node_id, hash8(payload_hash))
        if (suspend_dir / "payload.json").exists():
            return
        rel = f"{suspend_dir.relative_to(self.run_dir.root).as_posix()}/payload.json"
        self.run_dir.write_atomic(rel, payload_text)

    async def apply(
        self, spec: NodeSpec, *, intents: Sequence[HasDedupKey], kind: str, perform: Callable[[HasDedupKey], str]
    ) -> ResultRecord:
        """Perform each staged intent exactly once, guarded by its ``done/<kind>/<key>`` marker.

        Args:
            spec: The apply node's spec.
            intents: The same intents a prior :meth:`stage` call staged.
            kind: The intent kind; also the marker subdirectory, ``done/<kind>/``.
            perform: Does the one real effect (e.g. a push); called at most once per
                dedup key, ever, across every apply call that ever names that key.

        Returns:
            ``done``, with ``key_facts["outcomes"]`` mapping each dedup key to either
            what ``perform`` returned, or ``"already-done"`` when its marker existed.
        """
        keys = [intent.dedup_key for intent in intents]
        input_obj = {"kind": kind, "keys": keys}

        async def body(node_dir: Path) -> NodeOutcome:
            outcomes = {intent.dedup_key: self._apply_one(kind, intent, perform) for intent in intents}
            return NodeOutcome(
                status=NodeStatus.DONE,
                key_facts={"outcomes": outcomes},
                typed_fields=["outcomes"],
                executor_used="code",
                model_used="-",
                effort_used="-",
            )

        return await self._dispatch(spec, brief=f"apply: {kind}", input_obj=input_obj, body=body)

    def _apply_one(self, kind: str, intent: HasDedupKey, perform: Callable[[HasDedupKey], str]) -> str:
        """Perform ``intent`` exactly once, guarded by its ``done/<kind>/<dedup_key>`` marker."""
        marker = self.run_dir.marker(kind, intent.dedup_key)
        if marker.exists():
            return "already-done"
        outcome = perform(intent)
        marker.touch()
        return outcome

    def fold_decisions(self) -> None:
        """Journal every decision file not already folded, then refresh the index.

        Called by ``run.py`` first thing on a relaunch (before dispatching anything), so
        every ``approve`` call in this run sees every decision recorded while the
        coordinator was not running. A decision is identified by (node id, PAYLOAD
        hash), so a node that was asked about two payloads folds two lines: skipping on
        node id alone would silently drop the answer to the newer question and leave
        ``approve`` reading the older verdict.

        Which file is already folded is decided from its FILENAME alone
        (:meth:`~agentdag.application.kernel.ports.RunDir.decision_files`, the short
        hash included), matched against every already-folded line's own hash
        shortened the same way (:func:`~agentdag.domain.keys.hash8`) - never by parsing
        the file first. So a decision file that becomes corrupted or unreadable AFTER
        it was folded never blocks a later launch: it is skipped before anything tries
        to open it. Which files under ``decisions/`` are decisions at all - and the
        reserved cancel shapes that are not - is
        :meth:`~agentdag.application.kernel.ports.RunDir.decision_files`'s job, not this
        one's; the layout belongs to the port.

        How many of the run's decisions were HUMAN ones (a run-summary field) is not
        tracked here: :attr:`dispatcher`'s index already holds every folded decision
        after this call, which is a per-RUN view (folded from the whole journal), so
        ``summary.py`` counts it from there rather than from a per-launch counter that
        would read zero on any relaunch that finds every decision already folded.
        """
        folded = {(node_id, hash8(payload_hash)) for node_id, payload_hash in self.dispatcher.index.decisions}
        for ref in self.run_dir.decision_files():
            if (ref.node_id, ref.short_hash) in folded:
                continue
            decision = self.run_dir.read_decision_file(ref)
            self.dispatcher.journal.append(
                ApproveDecisionLine(
                    node_id=decision.node_id,
                    decision=decision.decision,
                    reason=decision.reason,
                    by=decision.by,
                    token_id=decision.token_id,
                    payload_hash=decision.payload_hash,
                    at=stamp(self.clock),
                )
            )
        self.dispatcher.reload_decisions()

    async def _dispatch(self, spec: NodeSpec, *, brief: str, input_obj: Mapping[str, Any], body: Body) -> ResultRecord:
        """Dispatch through the run's dispatcher, then charge - the ONE path every primitive uses.

        A primitive on this coordinator must call THIS, never
        ``self.dispatcher.dispatch`` directly, so a record is charged exactly once
        whether it was just run or served from the journal on replay (:meth:`work`'s
        resumed-run test proves the served branch is charged too), and so every
        dispatched spec's write set is recorded in :attr:`declared_write_sets`
        BEFORE the body runs - :meth:`scan` reads that map, never re-derives it.

        Also the ONE place :attr:`sandbox`'s guarantees are READ for a dispatch (Task 19):
        every primitive routes through here, so every node - a work node dispatched through
        an :class:`~.ports.Executor` and a code node (gate, scan, reduce, ...) that never
        touches :attr:`sandbox` at all - carries the SAME declaration, because there is
        exactly one :class:`~.sandbox.Sandbox` wired per run. The declaration is handed to
        :meth:`~agentdag.application.kernel.dispatch.Dispatcher.dispatch`, which stamps it
        onto the record it BUILDS, before ``record.json`` is written and the journal's
        ``result`` line is appended - so what is actually persisted carries ``sandbox`` too,
        not only what this method returns. A key already served from the journal is
        returned untouched: it keeps whatever declaration it was ORIGINALLY dispatched
        under, even when THIS launch is wired with a different :class:`~.sandbox.Sandbox`.

        Args:
            spec: The node being dispatched.
            brief: The node's brief.
            input_obj: The assembled input.
            body: What to run when the journal has no result for this key.

        Returns:
            The record :meth:`~agentdag.application.kernel.dispatch.Dispatcher.dispatch`
            returned, already charged: freshly built with :attr:`sandbox`'s guarantees on
            it, or served from the journal with its own original declaration intact.
        """
        self.declared_write_sets[spec.node_id] = tuple(spec.write_set)
        record = await self.dispatcher.dispatch(
            spec, brief=brief, input_obj=input_obj, body=body, sandbox=self.sandbox.guarantees()
        )
        self._charge(record)
        return record

    def _charge(self, record: ResultRecord) -> None:
        """Add a record's charged tokens to the run's per-row totals.

        Nothing refuses here: the run-level cap that reads these totals before the NEXT
        dispatch is M3's mechanism, and this is the measurement it will read.
        """
        for row_name, charged in record.charged_tokens.items():
            self.tokens_by_row[row_name] = self.tokens_by_row.get(row_name, 0) + charged


def _validate_default(payload: ApprovePayload) -> None:
    """Refuse a payload whose default option is not ``effect == "none"`` (design 2.4).

    Raises:
        SpecRejected: ``payload.default`` names no option in ``payload.options``, or
            names one whose effect is ``"external"``.
    """
    options_by_id = {option.id: option for option in payload.options}
    default_option = options_by_id.get(payload.default)
    if default_option is None or default_option.effect != "none":
        raise SpecRejected(f"approve default {payload.default!r} does not name a no-effect option")


def _branch_record(map_id: str, index: int, outcome: ResultRecord | BaseException) -> ResultRecord:
    """Turn one :func:`asyncio.gather` outcome into its record: pass a real one through, wrap a raise.

    Args:
        map_id: The map this branch belongs to; names the synthetic node id.
        index: The branch's position in the item sequence :meth:`Coordinator.map` was given.
        outcome: What ``asyncio.gather(..., return_exceptions=True)`` collected for this branch.

    Returns:
        ``outcome`` unchanged when it is a real record; otherwise a synthetic ``failed``
        record for ``f"{map_id}@{index}"``, never journaled - it names no real dispatch.

    Raises:
        BaseException: ``outcome`` is a ``BaseException`` that is not an ``Exception``
            (a ``SystemExit``, a ``KeyboardInterrupt``, an ``asyncio.CancelledError``) -
            the coordinator process itself going away, re-raised rather than swallowed.
    """
    if not isinstance(outcome, BaseException):
        return outcome
    if not isinstance(outcome, Exception):
        raise outcome
    return ResultRecord(
        node_id=f"{map_id}@{index}",
        attempt=0,
        status=NodeStatus.FAILED,
        input_hash="-",
        duration_s=0.0,
        executor_used="-",
        model_used="-",
        effort_used="-",
        error=NodeError(type=ErrorType.EXECUTOR_ERROR, message=f"{type(outcome).__name__}: {outcome}", transient=True),
    )
