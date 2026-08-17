"""The coordinator context: what a workflow program is handed (design 3.2, section 4).

A workflow program is deterministic code over typed records. It never touches the world
directly: every effect it has goes through one of the primitives on :class:`Coordinator`,
and every primitive goes through :meth:`~agentdag.application.kernel.dispatch.Dispatcher.dispatch`,
so replay, the crash window, the journal key and the spend accounting are each defined in
exactly one place.

Contents:
    * :class:`Coordinator` - the primitives a workflow program calls, and the run-scoped
      state (interactions, tokens per model row) a run summary is written from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from .ports import ExecutorRequest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from pathlib import Path

    from pydantic import BaseModel

    from ...domain.models import ApprovePayload, Decision, NodeOutcome, NodeSpec, ResultRecord
    from ..graph_a_ports import GatePort, GitPort
    from .dispatch import Dispatcher
    from .ports import Clock, Executor, IsolationScanner, Policy, RunDir

__all__ = ["Coordinator"]

_ItemT = TypeVar("_ItemT")


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
        parallel: How many map branches may run at once.
        interactions: How many HUMAN decisions this run folded in - a run-summary field.
        tokens_by_row: Tokens charged per model row so far, summed from every record.
    """

    DEFAULT_PROMPT = (
        "Apply the change described in your system prompt to this repository. Commit with a clear message. Do not push."
    )
    """The prompt a work node runs under when the workflow does not name its own."""

    # Twelve keyword-only parameters because the coordinator IS its wiring: every seam a
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
        parallel: int,
    ) -> None:
        """Bind one run's wiring; ``interactions`` and ``tokens_by_row`` start empty."""
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
        self.parallel = parallel
        self.interactions = 0
        self.tokens_by_row: dict[str, int] = {}

    async def work(self, spec: NodeSpec, *, brief: str, cwd: Path, prompt: str = DEFAULT_PROMPT) -> ResultRecord:
        """Dispatch one work node: an executor, running ``brief`` against ``cwd``.

        The policy resolves the spec to a model row before the key is computed, and the
        resolved row and executor are written back onto the dispatched spec - so a run
        under a changed policy is a different call, not a silently different node.

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
        """
        row = self.policy.resolve(spec)
        executor = self.executors[row.executor]
        input_obj = {
            "cwd": cwd.relative_to(self.run_dir.root).as_posix(),
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
        record = await self.dispatcher.dispatch(dispatched, brief=brief, input_obj=input_obj, body=body)
        self._charge(record)
        return record

    def snapshot(self) -> Mapping[str, str]:
        """Take the isolation-root manifest a later :meth:`scan` compares against.

        Returns:
            Relative POSIX path -> content hash, for everything under the run root the
            scanner watches. Taken BEFORE the node whose writes it will judge.
        """
        return self.scanner.snapshot(self.run_dir.root)

    async def gate(self, spec: NodeSpec, *, argv: Sequence[str], cwd: Path) -> ResultRecord:
        """Dispatch a mechanical gate: run ``argv`` in ``cwd`` and record its exit code.

        Raises:
            NotImplementedError: Task 13 fills this primitive.
        """
        raise NotImplementedError("Task 13")

    async def scan(self, spec: NodeSpec, *, watched: str) -> ResultRecord:
        """Dispatch the isolation-root scan as a gate node: writes outside the write set are the finding.

        Raises:
            NotImplementedError: Task 13 fills this primitive. It also widens this
                signature to take the ``before`` manifest from :meth:`snapshot` and the
                write set the diff is judged against.
        """
        raise NotImplementedError("Task 13")

    async def reduce(self, spec: NodeSpec, *, fold: Callable[[], NodeOutcome]) -> ResultRecord:
        """Dispatch a code fold: ``fold`` runs as the node's body and its outcome is the record.

        Raises:
            NotImplementedError: Task 13 fills this primitive.
        """
        raise NotImplementedError("Task 13")

    async def map(
        self, map_id: str, items: Sequence[_ItemT], body: Callable[[int, _ItemT], Awaitable[ResultRecord]]
    ) -> list[ResultRecord]:
        """Fan out over ``items``, at most :attr:`parallel` at once; one raising branch never kills the run.

        Raises:
            NotImplementedError: Task 13 fills this primitive.
        """
        raise NotImplementedError("Task 13")

    async def stage(self, spec: NodeSpec, *, intents: Sequence[BaseModel], kind: str) -> ResultRecord:
        """Write every intent under ``intents/<kind>/`` BEFORE anything leaves the process (design 3.4).

        Raises:
            NotImplementedError: Task 13 fills this primitive.
        """
        raise NotImplementedError("Task 13")

    async def approve(self, spec: NodeSpec, *, payload: ApprovePayload) -> Decision:
        """Return the recorded decision, or write the payload and suspend the run.

        Raises:
            NotImplementedError: Task 13 fills this primitive.
            Suspended: (once filled) no decision is recorded yet, so the coordinator exits.
        """
        raise NotImplementedError("Task 13")

    async def apply(
        self, spec: NodeSpec, *, intents: Sequence[BaseModel], kind: str, perform: Callable[[BaseModel], str]
    ) -> ResultRecord:
        """Perform each staged intent exactly once, guarded by its ``done/<kind>/<key>`` marker.

        Raises:
            NotImplementedError: Task 13 fills this primitive.
        """
        raise NotImplementedError("Task 13")

    def fold_decisions(self) -> None:
        """Journal every decision file the replay index does not hold yet, then rebuild the index.

        Raises:
            NotImplementedError: Task 13 fills this primitive.
        """
        raise NotImplementedError("Task 13")

    def _charge(self, record: ResultRecord) -> None:
        """Add a record's charged tokens to the run's per-row totals.

        Nothing refuses here: the run-level cap that reads these totals before the NEXT
        dispatch is M3's mechanism, and this is the measurement it will read.
        """
        for row_name, charged in record.charged_tokens.items():
            self.tokens_by_row[row_name] = self.tokens_by_row.get(row_name, 0) + charged
