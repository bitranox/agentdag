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
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from pydantic import BaseModel

from ...domain.handover import (
    HANDOVER_AS_WRITTEN_FILENAME,
    HANDOVER_FILENAME,
    prompt_with_stop_duty,
    stamp_identity,
)
from ...domain.journal import ApproveDecisionLine, RetryGrantLine
from ...domain.kernel_errors import KernelError, Suspended
from ...domain.keys import canonical_json, content_hash, hash8
from ...domain.models import (
    CODE_KINDS,
    Decision,
    ErrorType,
    MarkerPhase,
    NodeError,
    NodeOutcome,
    NodeStatus,
    ResultRecord,
    SuspendReason,
)
from ...domain.plan import PLAN_FILENAME
from ...domain.policy import FailureAction
from ...domain.scan import diff_manifests, stray_paths
from .approve import validate_approve_payload
from .ports import ExecutorRequest, stamp

_PROVIDER_REFUSALS: dict[ErrorType, SuspendReason] = {
    ErrorType.RATE_LIMITED: SuspendReason.QUOTA,
    ErrorType.AUTH_FAILURE: SuspendReason.CREDENTIAL,
}
"""The refusals that come from outside the run, and what a suspended run is then waiting for.

Membership is the test for "no retry or escalation can reach this": both bind the whole
account, so re-dispatching at the same rank or the next one up is refused identically.
Every other error type is the node's own and stays a record.
"""

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from ...domain.models import ApprovePayload, NodeSpec
    from ..graph_a_ports import GatePort, GitPort
    from .dispatch import Body, Dispatcher
    from .ports import Clock, Executor, IsolationScanner, Policy, RunDir
    from .registry import OpRegistry
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


class PerformIntent(Protocol):
    """Does one intent's real effect, and owns what to do when it may already have happened.

    The kernel cannot decide that for a workflow: whether a repeat is harmless depends on
    what the effect IS. So :meth:`Coordinator.apply` supplies the fact and this callable
    supplies the policy. ``may_have_landed`` is true exactly when a previous attempt on
    this dedup key wrote its ``attempted`` marker and never reached ``done`` - a crash
    between the effect and its record.

    An effect whose target can be read back ignores the flag and simply reads (graph A's
    ``perform_push`` compares the target ref, which answers the question for both cases).
    An effect that CANNOT be read back - a mail, a non-idempotent API call - has no other
    way to know, and should refuse rather than repeat.
    """

    def __call__(self, intent: HasDedupKey, *, may_have_landed: bool) -> str:
        """Perform ``intent`` and return what happened, as a short outcome word."""
        ...


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
        registry: OpRegistry,
        sandbox: Sandbox,
        parallel: int,
    ) -> None:
        """Bind one run's wiring; ``tokens_by_row`` starts empty.

        ``registry`` is here because a workflow program is handed the coordinator and
        nothing else, and a program that plans (:func:`~agentdag.application.kernel.root.run_root`)
        needs the ops a plan may name. Only
        :func:`~agentdag.composition.kernel.build_op_registry` builds one, and the layer
        contract forbids ``application`` importing ``composition``, so it arrives by
        injection or not at all.
        """
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
        self.registry = registry
        self.sandbox = sandbox
        self.parallel = parallel
        # Run-wide, not per-map: `parallel` is what this HOST may run at once (worktrees,
        # the shared bmk tool env, the executor's own concurrency), so a second concurrent
        # map must not double it. asyncio.Semaphore binds to the running loop lazily, on
        # the first `async with`, so building it here - outside the loop - is fine.
        self._map_semaphore = asyncio.Semaphore(parallel)
        self.tokens_by_row: dict[str, int] = {}
        self.declared_write_sets: dict[str, tuple[str, ...]] = {}

    def parallel_bound(self) -> asyncio.Semaphore:
        """Return the run-wide semaphore bounding how many dispatches run at once.

        The SAME object :meth:`map` uses, deliberately: ``parallel`` is what this HOST may
        run at once, so a plan's execute loop
        (:func:`~agentdag.application.kernel.execute.execute_plan`) and a concurrent map
        fan-out must share one bound rather than admit ``parallel`` each.

        A public accessor rather than the private attribute, because the loop lives in a
        sibling module and reading ``_map_semaphore`` from there is what pyright strict's
        ``reportPrivateUsage`` refuses. Do not hold it across a RECURSION: a caller that
        keeps a slot while a nested plan's own leaves queue for one deadlocks at
        ``parallel=1``.

        Returns:
            The run's shared semaphore.
        """
        return self._map_semaphore

    async def work(
        self,
        spec: NodeSpec,
        *,
        brief: str,
        cwd: Path,
        prompt: str = DEFAULT_PROMPT,
        is_stopping: Callable[[], bool] | None = None,
        workspace: Path | None = None,
    ) -> ResultRecord:
        """Dispatch one work node: an executor, running ``brief`` against ``cwd``.

        The policy resolves the spec to a model row before the key is computed, and the
        resolved row and executor are written back onto the dispatched spec - so a
        policy change that moves this spec's RESOLVED ROW (its model alias or its
        executor) is a different call, not a silently different node. ``max_turns``,
        ``deny_bash`` and ``deny_tools`` reach the executor request but are not part of the
        key or of ``input_obj``, so changing only those does not.

        The token cap has two call sites (design 7, M3): this method threads
        ``spec.budget.tokens.get(row.alias)`` through as ``ExecutorRequest.token_cap``,
        which the executor enforces per TURN by calling ``client.interrupt()`` once a
        turn's own usage passes it; and its ``body`` closure calls
        :meth:`_run_cap_refusal` first, which refuses the dispatch OUTRIGHT (a FAILED,
        ``BUDGET_EXCEEDED`` record, the executor never called) when this node's own cap
        would push the run's row total past ``policy.run_limits.tokens_per_row``. Both checks are a
        no-op for a node whose spec declares no cap for the resolved row.

        The node deadline (design 7, M3; a DIFFERENT quantity from the token cap - wall-clock
        seconds elapsed, never a token count) is threaded the same way:
        ``min(spec.deadline_s, policy.run_limits.deadline_ceiling_s)`` reaches ``ExecutorRequest.deadline_s``,
        clamped HERE rather than left to the executor, so the executor never has to know
        about the run-limit ceiling at all - it only ever compares elapsed time against the
        one already-clamped figure it was handed. The clamp is silent (no journal line): the
        planner-driven ``clamp`` event of design 2.3 rule 4 is out of scope here, same as the
        other three run-limit clamps ``domain.policy`` already documents as such.

        The resolved row naming an executor :attr:`executors` does not carry (the tier
        policy table describes the full target deployment; wiring an executor is a
        separate, incremental step - a policy row can legitimately be ``available``
        before its executor exists) is ALSO checked inside ``body``, the same shape as
        :meth:`_run_cap_refusal`: it raises :class:`~agentdag.domain.kernel_errors.KernelError`,
        which :func:`~agentdag.application.kernel.dispatch._run_body`'s own guard turns
        into a ``FAILED``, ``transient=False`` record - never an exception escaping this
        method uncaught, which would abort the WHOLE run over one misresolved node
        rather than fail just that node with a record the workflow (and the operator)
        can see. The trade-off, taken deliberately: unlike the cwd check below, fixing
        the wiring alone does not change this node's identity, so a plain resume with
        the SAME spec is served this SAME failed record from the journal rather than
        re-attempting - an operator who fixes the wiring re-dispatches with a bumped
        ``spec.attempt`` (an identity field) or starts a fresh run, not a bare resume.

        Args:
            spec: The node spec, with its tier role, write set, deps and limits.
            brief: The node's brief; its content hash is part of the journal key.
            cwd: The working directory the executor runs in; recorded in the key as a
                path relative to the run root, so a run directory that moves is still
                the same call.
            prompt: What the executor is told to do with the brief.
            is_stopping: Whether this node's SUBTREE has asked it to hand over, read at the
                executor's turn seam (``ExecutorRequest.is_stopping``). ``None`` for a
                dispatch belonging to no subtree. A predicate rather than a bool because the
                subtree decides while the node is already running.
            workspace: The operator-supplied workspace this run works in, already resolved,
                or ``None`` for a run confined to its own directory. It widens the roots
                ``cwd`` may lie under by exactly one AND reaches the executor as
                ``ExecutorRequest.extra_roots``, which is what makes the node's writes there
                permitted rather than denied by the write hook. Passed per call rather than
                held on the coordinator so a call site that forgets it FAILS here - the
                workspace ``cwd`` it did pass is then authorised by nothing and refused,
                loudly, instead of being dispatched with a boundary nobody widened. That
                pairing is what makes the omission loud, so it holds only for the primitives
                that take a ``cwd``; :meth:`scan` takes none, and forgetting it there is
                silent.

        Returns:
            The node's result record, with this node's charged tokens already added to
            :attr:`tokens_by_row`.

        Raises:
            KernelError: ``cwd`` sits outside every root this dispatch was given - a misconfiguration in
                whatever BUILT the request, so it raises HERE, before anything is
                dispatched: a failed record for it would just be retried forever
                (fixing it changes ``cwd``, hence the call's own identity, so nothing
                a bare resume could ever re-serve usefully).
        """
        dispatched, input_obj, body = self._executor_call(
            spec, brief=brief, cwd=cwd, prompt=prompt, is_stopping=is_stopping, workspace=workspace
        )
        return await self._dispatch(dispatched, brief=brief, input_obj=input_obj, body=body)

    async def plan_node(
        self,
        spec: NodeSpec,
        *,
        brief: str,
        cwd: Path,
        prompt: str = DEFAULT_PROMPT,
        is_stopping: Callable[[], bool] | None = None,
        workspace: Path | None = None,
    ) -> ResultRecord:
        """Dispatch a planner node and surface the ``plan.json`` it wrote, if it wrote one.

        It differs from :meth:`work` in exactly two respects. The second is that a planner
        node's reads are CONFINED to its own node directory and ``cwd``
        (``confine_reads=True``), which also refuses it Bash. A planner receives its whole
        input in its prompt and its brief and produces one file, so it has no business
        outside those two directories - and the first real ``plan-goal`` run, 2026-09-02,
        measured what happens without the bound: it read an unrelated project's scratch
        files and then ran ``find /`` over the machine. A work node is NOT confined, because
        working on a real tree is what it is for.

        The first is that after the executor returns,
        this reads the node's own artefact dir for :data:`~agentdag.domain.plan.PLAN_FILENAME`
        and adds its run-relative path to ``artefact_refs``. That read is the whole reason the
        primitive exists. ``node_dir`` is created by the dispatcher and never leaves
        :meth:`_dispatch`'s ``body``, the executor's own ``artefact_refs`` hold the node's CWD
        rather than anything in that dir, and a node dir is not derivable from a record either
        (:func:`~agentdag.domain.keys.journal_key` needs ``brief_hash`` and ``prefix``, and a
        :class:`~agentdag.domain.models.ResultRecord` carries neither). So a caller that needs
        a file the node left behind has to be inside the body, which is here.

        Same shape as :meth:`gate`, which surfaces ``gate.log`` the same way. A node that
        wrote no plan gets no such ref, so "planned nothing" stays distinguishable from
        "planned something I could not find" - the caller turns the first into typed reasons.

        Args:
            spec: The planner node's spec.
            brief: The goal and evidence, as the node's brief.
            cwd: The working directory the planner runs in.
            prompt: What the executor is told to do with the brief.
            is_stopping: As :meth:`work`. A planner node is as stoppable as any other: its
                subtree can be abandoned while it is still writing a plan nobody will run.
            workspace: As :meth:`work`. A planner's reads are confined to its node directory
                and ``cwd``, so a planner planning FOR a workspace reads inside it and
                nowhere else.

        Returns:
            The planner node's record, carrying the plan's path when there is one.
        """
        dispatched, input_obj, body = self._executor_call(
            spec,
            brief=brief,
            cwd=cwd,
            prompt=prompt,
            is_stopping=is_stopping,
            confine_reads=True,
            workspace=workspace,
        )
        return await self._dispatch(
            dispatched, brief=brief, input_obj=input_obj, body=self._also_surfacing_the_plan(body)
        )

    def _also_surfacing_the_plan(self, body: Body) -> Body:
        """Wrap ``body`` so a ``plan.json`` in the node dir joins the outcome's artefact refs.

        Absent file, absent ref: the outcome is returned untouched. Nothing here parses or
        repairs what the node wrote - a malformed plan is the node's report to make, and the
        caller turns it into reasons - so this only ever answers "is it there, and where".
        """

        async def surfacing(node_dir: Path) -> NodeOutcome:
            outcome = await body(node_dir)
            if not (node_dir / PLAN_FILENAME).exists():
                return outcome
            rel = f"{node_dir.relative_to(self.run_dir.root).as_posix()}/{PLAN_FILENAME}"
            return outcome.model_copy(update={"artefact_refs": [*outcome.artefact_refs, rel]})

        return surfacing

    def _executor_call(
        self,
        spec: NodeSpec,
        *,
        brief: str,
        cwd: Path,
        prompt: str,
        is_stopping: Callable[[], bool] | None = None,
        confine_reads: bool = False,
        workspace: Path | None = None,
    ) -> tuple[NodeSpec, dict[str, Any], Body]:
        """Resolve the row and build the dispatched spec, the input object and the body.

        Shared by :meth:`work` and :meth:`plan_node`, which differ only in what they do with
        the body afterwards. Everything :meth:`work`'s own docstring describes - row
        resolution before the key, the token cap's two call sites, the deadline clamp, the
        unwired-executor check inside the body - happens here and is documented there.

        Args:
            spec: The node spec, with its tier role, write set, deps and limits.
            brief: The node's brief; its content hash is part of the journal key.
            cwd: The working directory the executor runs in.
            prompt: What the executor is told to do with the brief.
            workspace: The operator-supplied workspace, as :meth:`work` documents it.
            confine_reads: Whether this node may only read inside its own node directory
                and ``cwd``. False for a node that has to work on a real tree; true for a
                node whose whole input arrives in its prompt, where an unconfined read is
                only ever an excursion. The roots are built inside the body because
                ``node_dir`` does not exist until then.

        Returns:
            The spec as dispatched (row and executor written back), the input object, and
            the body to run when the journal has no record for the key.

        Raises:
            KernelError: ``cwd`` sits outside every root this dispatch was given, or
                ``workspace`` lies inside the run root (:meth:`_recorded_cwd`).
        """
        row = self.policy.resolve(spec)
        input_obj = {
            "cwd": self._recorded_cwd(cwd, workspace=workspace, node_id=spec.node_id),
            "prompt": prompt,
            "model": row.alias,
            "effort": spec.effort,
        }
        # An absent budget takes the policy's default rather than exempting the node. Every
        # PLANNER-emitted entry arrives with an empty `budget` - the plan schema does not
        # require one and no shipped rule adds one - so without this the entire model-driven
        # path was uncapped per node and only the run-wide row ceiling bound it (OPEN-WORK 55).
        declared = spec.budget.tokens.get(row.alias)
        node_cap = self.policy.default_node_tokens if declared is None else declared
        node_deadline_s = min(spec.deadline_s, self.policy.run_limits.deadline_ceiling_s)

        async def body(node_dir: Path) -> NodeOutcome:
            if row.executor not in self.executors:
                raise KernelError(
                    f"row {row.alias!r} (node {spec.node_id!r}) resolved to executor "
                    f"{row.executor!r}, which is not wired; wired: {sorted(self.executors)}"
                )
            executor = self.executors[row.executor]
            refusal = self._run_cap_refusal(row.alias, node_cap)
            if refusal is not None:
                return NodeOutcome(
                    status=NodeStatus.FAILED,
                    executor_used=row.executor,
                    model_used=row.alias,
                    effort_used="-",
                    error=refusal,
                )
            # The duty rides in the PROMPT, not the brief and not a hook. Measured over 40
            # dispatches (RESEARCH probes/handover-nudge-inject.md, decision 14): a stop
            # notice with no prior standing in the task is refused 4 of 4 as prompt
            # injection, and a hook cannot confer that standing because a hook is the very
            # channel the node discounts. The path is absolute and inside node_dir, which
            # `allowed_writes` already grants, so the write-set hook cannot deny it.
            request = ExecutorRequest(
                node_dir=node_dir,
                cwd=cwd,
                brief=brief,
                prompt=prompt_with_stop_duty(prompt, handover_path=str(node_dir / HANDOVER_FILENAME)),
                model=row.alias,
                effort=spec.effort,
                max_turns=self.policy.max_turns,
                isolation_root=self.run_dir.root,
                extra_roots=() if workspace is None else (workspace,),
                write_set=tuple(spec.write_set),
                deny_bash=self.policy.deny_bash,
                deny_tools=self.policy.deny_tools,
                read_roots=(node_dir, cwd) if confine_reads else None,
                token_cap=node_cap,
                deadline_s=node_deadline_s,
                handover_at_tokens=row.handover_at_tokens,
                is_stopping=is_stopping,
            )
            return self._suspended_if_the_provider_refused(spec, await executor.run(request))

        return spec.model_copy(update={"executor": row.executor, "model": row.alias}), input_obj, body

    def _suspended_if_the_provider_refused(self, spec: NodeSpec, outcome: NodeOutcome) -> NodeOutcome:
        """Turn a refusal that came from OUTSIDE the run into a suspend, when policy allows it.

        Quota and a rejected credential are not this node's failure and no retry or
        escalation reaches them: the limit and the identity both bind the whole account, so
        the next rank up is refused in exactly the same way. What they have in common is
        that the obstacle can be waited out or repaired, which makes ending the launch
        RESUMABLE strictly better than ending it dead - every finished node survives.

        Raising rather than returning is load-bearing. The dispatcher records whatever the
        body returns, and :func:`~agentdag.application.kernel.replay.build_replay_index`
        serves every recorded result on replay whatever its status, so a returned refusal
        would be read straight back by the resume it is supposed to enable. Raising leaves
        the ``started`` line unmatched, which is the only shape a resume re-dispatches.

        Args:
            spec: The node the provider refused; names the cursor the run suspends at.
            outcome: What the executor reported.

        Returns:
            ``outcome`` unchanged unless the provider refused AND policy says suspend.

        Raises:
            Suspended: the provider refused and this run may end resumably.
        """
        if outcome.error is None:
            return outcome
        reason = _PROVIDER_REFUSALS.get(outcome.error.type)
        if reason is None:
            return outcome
        # Read and compared in the one function deliberately: routing this through a helper
        # that RETURNS the setting hides the comparison from static enforcement checks, and
        # a knob nothing can be shown to compare is how on_auth_failure sat inert for months.
        if outcome.error.type is ErrorType.RATE_LIMITED:
            action = self.policy.on_rate_limit
        else:
            action = self.policy.on_auth_failure
        if action is FailureAction.FAIL_RUN:
            return outcome
        raise Suspended(spec.node_id, reason=reason)

    def _chain_limit_refusal(self, spec: NodeSpec) -> NodeError | None:
        """Whether this link is one handover past what ``policy.max_continuations`` allows (3.8).

        Checked HERE, in the body, rather than in :meth:`_continues`, so that running out
        of links produces a record and a journal line like any other refusal: a run can
        then say why a chain stopped instead of merely having no more records after the
        last handover. It is the same shape as :meth:`_run_cap_refusal` for the same
        reason, and it is checked BEFORE that one because a chain with no links left
        cannot spend anything either way.

        The comparison is strict on the COUNT OF HANDOVERS, not on the link number:
        ``max_continuations`` is how many handovers a chain may take, so a chain reaching
        ``continuation == max_continuations`` has taken exactly its allowance and still
        runs; the link after it is the one refused.

        Args:
            spec: The node about to be dispatched, carrying its ``continuation``.

        Returns:
            A ``CONTINUATION_LIMIT`` error when this link is past the allowance, else
            ``None``. Not transient: another attempt cannot give the chain more links.
        """
        if spec.continuation <= self.policy.max_continuations:
            return None
        return NodeError(
            type=ErrorType.CONTINUATION_LIMIT,
            message=(
                f"node {spec.node_id!r} handed over {spec.continuation} times; "
                f"policy allows {self.policy.max_continuations}"
            ),
            transient=False,
        )

    def _run_cap_refusal(self, row: str, node_cap: int | None) -> NodeError | None:
        """Whether dispatching now, with ``node_cap`` on ``row``, would push the run past its ceiling.

        Checked freshly at BODY-EXECUTION time (called from inside :meth:`work`'s own
        ``body`` closure, not precomputed before ``_dispatch`` is awaited) so a concurrent
        map branch's own charge - landed between this call being queued and it actually
        running - is reflected here rather than read stale (design 7: "evaluated before
        the NEXT dispatch"). The check is against the node's OWN DECLARED CAP, not what it
        might actually spend: a node that stays well under its cap still could not have
        been allowed to start once its cap alone would tip the row over, because the
        coordinator has no way to promise it will not use the whole of what it declared.

        Both sides of the comparison are the SAME unit: a dispatch's total SPEND (input
        total plus output tokens, summed across its whole turn stream), never a single
        turn's context size. ``tokens_by_row`` is built by summing each recorded
        ``charged_tokens`` (:func:`~agentdag.adapters.kernel.executor_claude.outcome_from_usage`
        and :meth:`~agentdag.adapters.kernel.executor_claude.ClaudeExecutor._budget_outcome`
        both compute that as one dispatch's input-plus-output total), and ``node_cap``
        is ``request.token_cap`` - the same figure
        :meth:`~agentdag.adapters.kernel.executor_claude.ClaudeExecutor._on_turn` enforces
        against its own running sum of that dispatch's turns (see that method's
        docstring for why a per-turn context figure could not serve here instead).

        Args:
            row: The resolved model row alias (``ResolvedRow.alias``).
            node_cap: This node's own cap for ``row`` (``NodeSpec.budget.tokens.get(row)``),
                or ``None`` when the node declares no cap for this row at all - nothing to
                check here (the run-level cap has nothing to add against; the per-node
                turn-seam check in the executor is the same "no cap declared, nothing
                enforced" rule).

        Returns:
            A ``BUDGET_EXCEEDED`` :class:`~agentdag.domain.models.NodeError` when
            ``tokens_by_row[row] + node_cap`` would exceed ``policy.run_limits.tokens_per_row[row]``,
            else ``None`` - also ``None`` when ``policy.run_limits.tokens_per_row`` declares no
            ceiling for ``row`` at all (an operator who did not cap a row is not capping
            it here either).
        """
        if node_cap is None:
            return None
        ceiling = self.policy.run_limits.tokens_per_row.get(row)
        if ceiling is None:
            return None
        charged = self.tokens_by_row.get(row, 0)
        if charged + node_cap <= ceiling:
            return None
        return NodeError(
            type=ErrorType.BUDGET_EXCEEDED,
            message=(
                f"row {row!r} already charged {charged} of {ceiling}; this node's own "
                f"cap {node_cap} would push the run past its ceiling"
            ),
            transient=False,
        )

    def _recorded_cwd(self, cwd: Path, *, workspace: Path | None, node_id: str) -> str:
        """Name ``cwd`` the way this dispatch's key will, refusing one under no root it was given.

        The run root's own paths are named RELATIVE to it, so a run directory that moves is
        still the same call. A ``workspace`` is the one other root a dispatch may be given
        (design 2.1, C8), and a path under it is named ABSOLUTELY: it has no relative form,
        and a workspace that moved is a different directory rather than the same one under a
        new name.

        Args:
            cwd: The working directory the node will run in.
            workspace: The operator-supplied workspace this dispatch may work in, already
                resolved, or ``None`` for a dispatch confined to the run root alone.
            node_id: Whose dispatch this is; named in the refusal so an operator can see
                which node was wired wrong.

        Returns:
            The path as the dispatch's ``input_obj`` records it.

        Raises:
            KernelError: ``workspace`` lies inside the run root, or ``cwd`` lies outside
                every root this dispatch was given. Both are misconfigurations in whatever
                BUILT the call, so they are raised HERE, before anything is dispatched.
        """
        if workspace is not None and self._within_run_root(workspace):
            raise KernelError(
                f"workspace {workspace} of node {node_id!r} is inside the run root "
                f"{self.run_dir.root}; a workspace is a SECOND root, and the isolation scan "
                f"does not watch it - one under the run root would be watched and every write "
                f"the plan made there would read as a stray write"
            )
        if self._within_run_root(cwd):
            return _real(cwd).relative_to(_real(self.run_dir.root)).as_posix()
        if workspace is not None and _is_within(cwd, workspace):
            return cwd.as_posix()
        named = f" and outside the workspace {workspace}" if workspace is not None else ""
        raise KernelError(f"cwd {cwd} of node {node_id!r} is outside the run root {self.run_dir.root}{named}")

    def _within_run_root(self, path: Path) -> bool:
        """Return whether ``path`` is the run root or sits under it, judged after ``realpath``."""
        return _is_within(path, self.run_dir.root)

    def snapshot(self) -> Mapping[str, str]:
        """Take the isolation-root manifest a later :meth:`scan` compares against.

        The run root and nothing else. A ``workspace`` a dispatch was given is therefore
        never in the manifest, and that is structural rather than incidental:
        :meth:`_recorded_cwd` refuses a workspace inside the run root, so there is no way to
        name one this walk could reach. :meth:`scan` records the exclusion on the scan it
        judges, which is where a reader of the run meets it.

        Returns:
            Relative POSIX path -> content hash, for everything under the run root the
            scanner watches. Taken BEFORE the node whose writes it will judge.
        """
        return self.scanner.snapshot(self.run_dir.root)

    async def gate(self, spec: NodeSpec, *, cwd: Path, workspace: Path | None = None) -> ResultRecord:
        """Dispatch a mechanical gate: run :attr:`gate_port`'s command in ``cwd``, record its exit code.

        The argv comes from the PORT and nowhere else, which is what makes the recorded
        command the executed one: a caller that states it separately can state a DIFFERENT
        one, and nothing downstream can tell - the journal key, the node's ``input.json``
        and its brief would all name a command the machine never ran. It is part of
        ``input_obj``, so the same node under a differently wired gate is a new dispatch
        rather than a replay of the old verdict.

        Args:
            spec: The gate node's spec.
            cwd: The working directory the gate runs in; recorded in the key exactly as
                :meth:`work`'s ``cwd`` is - relative to the run root, or absolute under a
                named ``workspace``.
            workspace: The operator-supplied workspace this run works in, or ``None``. A
                gate runs where the work happened, so it is bounded by the same roots that
                work was: without this a plan working in a workspace could not gate itself.

        Returns:
            The gate's record: ``done`` on exit code 0, else ``failed``, with the exit
            code in ``key_facts["rc"]`` and the gate's combined output at
            ``artefact_refs[0]``.

        Raises:
            KernelError: ``cwd`` sits outside every root this dispatch was given.
        """
        cwd_rel = self._recorded_cwd(cwd, workspace=workspace, node_id=spec.node_id)
        argv = self.gate_port.command
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
        self,
        spec: NodeSpec,
        *,
        watched: str,
        before: Mapping[str, str],
        write_set: Sequence[str],
        workspace: Path | None = None,
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

        A ``workspace`` is NOT watched at all. This scan diffs the run root, and a workspace
        is a second root outside it (refused inside it by :meth:`_recorded_cwd`), so every
        write the plan made there is a write no scan judged. That is the cost of working
        outside the run's own directory, and a clean verdict from a run that named one says
        strictly less than a clean verdict from one that did not - so every scan states what
        it did not cover, in BOTH places a reader looks. As ``key_facts["unwatched_roots"]``
        it is on the journal's own result line, which is how a verdict in this system is
        read; it is empty on a run with no workspace, so a fold never has to tell a missing
        key from an empty one. As an ``input_obj`` entry it is in the dispatch KEY, so a scan
        of a workspace run can never be replay-served from a run without one.

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
            workspace: The operator-supplied workspace the watched node worked in, or
                ``None``. Recorded, never scanned - see the paragraph above.

        Returns:
            ``done`` when nothing strayed (``key_facts["stray"] == []``), else ``failed``
            with the stray paths in ``key_facts["stray"]``. Either way
            ``key_facts["unwatched_roots"]`` names the roots this scan did not cover.
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
            # Everything the COORDINATOR or an OPERATOR writes inside the run root. The scan
            # works from a content diff and cannot say whose write it saw, so a path here that
            # no node can reach is a path a node must never be blamed for. `decisions/` and
            # `retries/` are the two an operator writes into a LIVE run (run approve, run
            # cancel, run retry), so leaving either out fails whichever branch's scan window
            # happens to span the moment somebody answered.
            "nodes/**",
            "manifest/**",
            "intents/**",
            "artefacts/**",
            "done/**",
            "attempted/**",
            "decisions/**",
            "retries/**",
            "wt/.partial-*/**",  # a staging clone mid-rename: coordinator bookkeeping, not a node write
        ]
        unwatched = [workspace.as_posix()] if workspace is not None else []
        input_obj: dict[str, Any] = {"watched": watched, "write_set": list(write_set)}
        if unwatched:
            # Also an INPUT, and the key is omitted when there is nothing to say so a run with
            # no workspace keys exactly as it did before this field existed. In the key it does
            # what the fact below cannot: a scan of a workspace run can never be replay-served
            # from a run without one, however alike the two otherwise look.
            input_obj["unwatched_roots"] = list(unwatched)

        async def body(node_dir: Path) -> NodeOutcome:
            after = dict(self.scanner.snapshot(self.run_dir.root))
            stray = stray_paths(diff_manifests(dict(before), after), allowed=allowed)
            return NodeOutcome(
                status=NodeStatus.DONE if not stray else NodeStatus.FAILED,
                # Emitted on EVERY scan, empty included: a fold reading the journal decides
                # what a clean verdict is worth from this key, and an absent key would make
                # "covered everything" indistinguishable from a scan predating the field.
                key_facts={"stray": stray, "unwatched_roots": list(unwatched)},
                typed_fields=["stray", "unwatched_roots"],
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
            SpecRejected: the payload fails a design 2.4 approve rule - ``payload.default``
                does not name an option whose ``effect == "none"`` (a default the
                coordinator could apply unattended must never itself leave the process),
                or its operator-facing text is one no person may be shown to decide on.
            Suspended: no decision is recorded yet for this (node id, payload hash);
                the exception carries the hash, so a caller knows WHICH payload to ask about.
        """
        validate_approve_payload(payload)
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
        self, spec: NodeSpec, *, intents: Sequence[HasDedupKey], kind: str, perform: PerformIntent
    ) -> ResultRecord:
        """Perform each staged intent, recorded in two phases around the effect.

        ``attempted/<kind>/<key>`` is written BEFORE the effect and ``done/<kind>/<key>``
        after it. A single done-marker cannot describe the window between them, because it
        only exists once the effect has returned: a process death in there leaves an
        irreversible effect applied with nothing recording it, and the replay re-dispatches
        the node. The pair says, per dedup key, whether THIS effect may already have
        happened - which the journal's own crash window cannot, since it is per NODE and
        one apply node carries every intent its stage node staged.

        The kernel supplies that fact and does not act on it: whether a repeat is harmless
        depends on what the effect is, so the policy belongs to ``perform`` (see
        :class:`PerformIntent`).

        Args:
            spec: The apply node's spec.
            intents: The same intents a prior :meth:`stage` call staged.
            kind: The intent kind; also the marker subdirectory under each phase.
            perform: Does the one real effect (e.g. a push), and decides what a possible
                repeat means. It is not called again once ``done`` exists.

        Returns:
            ``done``, with ``key_facts["outcomes"]`` mapping each dedup key to either
            what ``perform`` returned, or ``"already-done"`` when its ``done`` marker
            existed, and ``key_facts["resumed"]`` listing the keys whose effect may have
            landed before a crash.
        """
        keys = [intent.dedup_key for intent in intents]
        input_obj = {"kind": kind, "keys": keys}

        async def body(node_dir: Path) -> NodeOutcome:
            resumed = [key for key in keys if self._may_have_landed(kind, key)]
            outcomes = {intent.dedup_key: self._apply_one(kind, intent, perform) for intent in intents}
            return NodeOutcome(
                status=NodeStatus.DONE,
                key_facts={"outcomes": outcomes, "resumed": resumed},
                typed_fields=["outcomes", "resumed"],
                executor_used="code",
                model_used="-",
                effort_used="-",
            )

        return await self._dispatch(spec, brief=f"apply: {kind}", input_obj=input_obj, body=body)

    def _may_have_landed(self, kind: str, key: str) -> bool:
        """Report whether a previous attempt on ``key`` reached its effect but not its record."""
        attempted = self.run_dir.marker(kind, key, phase=MarkerPhase.ATTEMPTED)
        done = self.run_dir.marker(kind, key, phase=MarkerPhase.DONE)
        return attempted.exists() and not done.exists()

    def _apply_one(self, kind: str, intent: HasDedupKey, perform: PerformIntent) -> str:
        """Perform ``intent`` once, between its ``attempted`` and ``done`` markers."""
        done = self.run_dir.marker(kind, intent.dedup_key, phase=MarkerPhase.DONE)
        if done.exists():
            return "already-done"
        may_have_landed = self._may_have_landed(kind, intent.dedup_key)
        # Written BEFORE the effect: a crash after this point is what makes the next
        # launch able to say the effect may have happened, rather than assuming it did not.
        self.run_dir.marker(kind, intent.dedup_key, phase=MarkerPhase.ATTEMPTED).touch()
        outcome = perform(intent, may_have_landed=may_have_landed)
        done.touch()
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

    def fold_retry_grants(self) -> None:
        """Journal every retry grant file not already folded, then refresh the index.

        Called by ``run.py`` on a relaunch beside :meth:`fold_decisions`, and for the same
        reason: an operator records a grant while no coordinator is running, so the launch
        that acts on it is the one that must fold it in - before anything dispatches, so the
        grant is already in the index when the failed node's record is served back.

        A grant is identified by (node id, KEY), matched against the pairs already folded the
        same way :meth:`fold_decisions` matches its own, so a grant file that becomes unreadable
        AFTER it was folded is skipped before anything tries to open it. Matching the short hash
        alone would be strictly weaker than the decision fold: two grants for different nodes
        whose keys collide in eight hex characters would leave the second unfolded for ever.
        Which files under ``retries/`` are grants at all is
        :meth:`~agentdag.application.kernel.ports.RunDir.retry_grant_files`'s job; the layout
        belongs to the port.

        Nothing is ever removed. A folded grant stays in the journal for ever, which is what
        makes a later replay re-make the same decisions in the same order, and it cannot cause
        a second retry: the attempt it authorises runs under ``attempt + 1`` and so lands on a
        different key.
        """
        folded = {(node_id, hash8(key)) for node_id, key in self.dispatcher.index.grants}
        for ref in self.run_dir.retry_grant_files():
            if (ref.node_id, ref.short_hash) in folded:
                continue
            granted = self.run_dir.read_retry_grant_file(ref)
            self.dispatcher.journal.append(
                RetryGrantLine(
                    node_id=granted.node_id,
                    key=granted.key,
                    reason=granted.reason,
                    by=granted.by,
                    token_id=granted.token_id,
                    at=stamp(self.clock),
                )
            )
        self.dispatcher.reload_grants()

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

        Also where a failure earns another attempt (M3): the loop below re-dispatches the SAME
        spec with ``attempt + 1`` while :meth:`_retries` says so, which is a different journal
        key and therefore a genuine re-run rather than the old record served back. The decision
        is a pure function of the record just returned and of what the JOURNAL holds - the
        automatic rule plus any folded ``retry_grant`` - so a replay makes the same choices in
        the same order.

        Returns:
            The record of the LAST attempt, already charged: freshly built with
            :attr:`sandbox`'s guarantees on it, or served from the journal with its own
            original declaration intact. Every earlier attempt keeps its own record and its
            own journal lines.
        """
        self.declared_write_sets[spec.node_id] = tuple(spec.write_set)
        record = await self._dispatch_once(spec, brief=brief, input_obj=input_obj, body=body)
        while True:
            if self._retries(spec, record):
                spec = spec.model_copy(update={"attempt": spec.attempt + 1})
            elif self._continues(record):
                # A successor is a FRESH dispatch of the next link, so its attempt counter
                # starts over: its own transient failures deserve the same allowance any
                # node gets. The chain is bounded by max_continuations instead, so the
                # total is bounded by both and by neither alone.
                spec = spec.model_copy(update={"continuation": spec.continuation + 1, "attempt": 0})
                limit = self._chain_limit_refusal(spec)
                if limit is not None:
                    # Dispatch a body that REFUSES rather than returning a record built
                    # here: the refusal then goes through the journal like every other
                    # record, so a replay makes the same decision instead of re-deriving
                    # it, and a run can say why the chain stopped.
                    body = _chain_limit_body(limit, spec)
            else:
                return record
            record = await self._dispatch_once(spec, brief=brief, input_obj=input_obj, body=body)

    async def _dispatch_once(
        self, spec: NodeSpec, *, brief: str, input_obj: Mapping[str, Any], body: Body
    ) -> ResultRecord:
        """Run ONE attempt through the dispatcher and charge whatever it returned.

        Args:
            spec: The node being dispatched, carrying the attempt number.
            brief: The node's brief.
            input_obj: The assembled input.
            body: What to run when the journal has no result for this key.

        Returns:
            The record, freshly built or served from the journal, already charged.
        """
        record = await self.dispatcher.dispatch(
            spec, brief=brief, input_obj=input_obj, body=self._stamping(spec, body), sandbox=self.sandbox.guarantees()
        )
        self._charge(record)
        return record

    def _stamping(self, spec: NodeSpec, body: Body) -> Body:
        """Wrap ``body`` so a handover record gets the coordinator's identity keys (decision 16).

        Two properties this placement buys, neither of which a simpler one has.

        It stamps with the CURRENT spec. ``body`` closes over ``work()``'s original spec, so
        nothing the dispatch loop increments is visible inside it - ``spec.continuation`` read
        there is 0 for ever, the same trap the chain limit hit. The wrapper closes over
        :meth:`_dispatch_once`'s argument instead, which is the spec being dispatched now.

        And it stamps only on a REAL dispatch. The dispatcher runs a body only when the journal
        has no record for the key, so a replayed handover keeps the identity it was written
        with rather than acquiring this run's - which is the difference between a record of what
        happened and a record that agrees with whoever asked last.

        Args:
            spec: The node being dispatched, carrying the current attempt and continuation.
            body: What to run when the journal has no result for this key.

        Returns:
            ``body``, followed by the stamp when it handed over.
        """

        async def stamped(node_dir: Path) -> NodeOutcome:
            outcome = await body(node_dir)
            if outcome.status is NodeStatus.NEEDS_CONTINUATION:
                self._stamp_handover(node_dir, spec)
            return outcome

        return stamped

    def _stamp_handover(self, node_dir: Path, spec: NodeSpec) -> None:
        """Re-persist the node's handover record with its identity keys, or leave it alone.

        The coordinator's only read of ``handover.json``, and the reason the record is JSON.
        Everything that can go wrong here is the node's report to make, not ours to repair: a
        node that handed over without writing a record, or wrote something unparseable, has
        already said so through its outcome and its schema shape. Rewriting either into
        something well-formed would manufacture a record no node produced, so both are left
        exactly as found - which is also why neither needs preserving: nothing overwrites them.

        The node's own bytes ARE preserved whenever this does overwrite, as
        ``handover.as-written.json``. Stamping reformats and reorders, and what a node wrote is
        the evidence every faithfulness question is answered from - questions that turn on its
        WORDING, not on its keys. The copy is written FIRST, so a crash between the two steps
        leaves the original readable rather than gone.

        Args:
            node_dir: The dispatch's artefact dir, holding the record the notice named.
            spec: The node as dispatched now.
        """
        node_rel = Path(node_dir.relative_to(self.run_dir.root))
        rel = str(node_rel / HANDOVER_FILENAME)
        try:
            raw = self.run_dir.read_text(rel)
        except (FileNotFoundError, OSError):
            return
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(record, dict):
            return
        stamped = stamp_identity(
            cast("dict[str, Any]", record),
            node_id=spec.node_id,
            attempt=spec.attempt,
            continuation=spec.continuation,
        )
        self.run_dir.write_atomic(str(node_rel / HANDOVER_AS_WRITTEN_FILENAME), raw)
        self.run_dir.write_atomic(rel, json.dumps(stamped, indent=2, sort_keys=True) + "\n")

    def _retries(self, spec: NodeSpec, record: ResultRecord) -> bool:
        """Return whether this failure earns another attempt: the automatic rule, or an operator's grant.

        Two INDEPENDENT reasons, deliberately not one widened rule. :meth:`_auto_retries` is
        decision 11 exactly as it shipped, so an unattended run behaves as it did before the
        grant existed; :meth:`_granted` is a person answering for a failure the automatic rule
        is right to refuse.

        Args:
            spec: The spec as dispatched, whose ``attempt`` is the one just run.
            record: What that attempt produced.

        Returns:
            Whether to dispatch ``attempt + 1``.
        """
        return self._auto_retries(spec, record) or self._granted(spec, record)

    def _granted(self, spec: NodeSpec, record: ResultRecord) -> bool:
        """Return whether an operator has granted this exact failed key another attempt (``run retry``).

        The only guard is that the record FAILED. Transience, kind and the attempt cap are
        :meth:`_auto_retries`'s business and are bypassed here on purpose: a red gate is a real
        answer to the machine, but a person who fixed the repo by hand changed something no
        journal key can see, and without this the failure is served back on every later launch.

        The match is on the (node id, key) PAIR, and the node id half is load-bearing. A
        journal key carries no node id (design 3.2's identity table), so two nodes whose work is
        identical share one key and the second is SERVED the first's record; matching the key
        alone would make one grant run the authorised attempt once PER twin - N dispatches and N
        charges - because a freshly granted key is by definition one the journal does not hold,
        so nothing serves the retried record to the second node. The cost of the pair is that a
        twin keeps the stale failure until it is granted too: a stale record, not repeated work.

        The node id compared is the SPEC's - the node being dispatched now - never the served
        record's, which in the dedup case names whichever node ran it first.

        This cannot loop. The attempt it authorises is dispatched under ``attempt + 1``, which
        is an identity field, so it produces a different key and the grant never matches twice.
        That is why a grant needs no counter and no consumed flag: one grant buys one attempt,
        and the journal line can stay folded for ever without changing a later replay.

        Args:
            spec: The node being dispatched; its ``node_id`` is half the grant's identity.
            record: The record just returned, freshly built or served from the journal.

        Returns:
            Whether an operator has granted this node another attempt for ``record``'s key.
        """
        if record.status is not NodeStatus.FAILED:
            return False
        return (spec.node_id, record.input_hash) in self.dispatcher.index.grants

    def _continues(self, record: ResultRecord) -> bool:
        """Whether this record earns a SUCCESSOR: it handed over at its context ceiling.

        Deliberately not a function of the spec, unlike :meth:`_retries`. A retry has to
        ask how many tries this node has already had; a continuation asks only what the
        record says, because the chain's bound is enforced where the successor is
        DISPATCHED (the body refuses past ``policy.max_continuations``) rather than here.
        Keeping the bound there means the refusal is journaled like any other node, so a
        run can explain why a chain stopped instead of simply having no more records.

        Args:
            record: The record just returned.

        Returns:
            Whether it ended ``needs_continuation``. No transience test and no kind test:
            a handover is never an error, and only a node that can hold context can
            produce one.
        """
        return record.status is NodeStatus.NEEDS_CONTINUATION

    def _auto_retries(self, spec: NodeSpec, record: ResultRecord) -> bool:
        """Return whether this failure earns another attempt on its own (M3's code-node retry).

        Three conditions, and each is load-bearing. The record must carry a TRANSIENT
        error: a red gate is ``FAILED`` with no ``error`` at all because it ran and
        reported a real answer, so retrying it would loop on a genuine test failure, and
        a :class:`~agentdag.domain.kernel_errors.KernelError` is stamped
        ``transient=False`` because the same inputs reproduce it
        (:func:`~agentdag.application.kernel.dispatch._run_body`). The kind must be one
        the coordinator runs as CODE: design 2.3 rule 5 owns a model node's retry and
        escalates a rank rather than repeating in place, so retrying one here would
        quietly do the thing that rule declined to do. And the attempt number must be
        under ``policy.max_attempts``, which is 2 in the shipped table - one retry,
        mirroring rule 5's single re-dispatch.

        This is deliberately NOT a new journal event. ``attempt`` is an identity field
        (design 3.2), so each attempt already appends its own ``started`` and ``result``
        lines under its own key: the failure is never overwritten, and a replay is served
        both records in order. A replay does depend on the POLICY, though - a table whose
        ``max_attempts`` was lowered since the run stops retrying earlier than the run it
        is replaying did, the same way every other policy-read value behaves.

        Args:
            spec: The spec as dispatched, whose ``attempt`` is the one just run.
            record: What that attempt produced.

        Returns:
            Whether to dispatch ``attempt + 1``.
        """
        if spec.attempt + 1 >= self.policy.max_attempts or spec.kind not in CODE_KINDS:
            return False
        return record.status is NodeStatus.FAILED and record.error is not None and record.error.transient

    def _charge(self, record: ResultRecord) -> None:
        """Add a record's charged tokens to the run's per-row totals.

        Nothing refuses here: the run-level cap that reads these totals before the NEXT
        dispatch is M3's mechanism, and this is the measurement it will read.
        """
        for row_name, charged in record.charged_tokens.items():
            self.tokens_by_row[row_name] = self.tokens_by_row.get(row_name, 0) + charged


def _real(path: Path) -> Path:
    """Return ``path`` with every symlink and relative segment resolved.

    Args:
        path: Any path a containment test is about to judge.

    Returns:
        Its ``realpath`` form.
    """
    return Path(os.path.realpath(path))


def _is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is ``root`` or sits under it, judged after ``realpath``.

    Both sides are resolved because neither arrives comparable by construction: a workspace is
    resolved where the argument is accepted, while the run root is whatever ``--runs`` named,
    carried verbatim through the run store. A textual prefix test between the two is therefore
    decided by how each was SPELLED - so a symlinked or relative runs directory defeats it,
    and with it the refusal of a workspace inside the run root. The write hook already judges
    every target by ``realpath``; this makes the coordinator's own boundary agree with it.

    Args:
        path: The path to judge.
        root: The root it must lie under.

    Returns:
        Whether the resolved ``path`` is the resolved ``root`` or sits beneath it.
    """
    real, real_root = _real(path), _real(root)
    return real == real_root or real_root in real.parents


def _chain_limit_body(error: NodeError, spec: NodeSpec) -> Body:
    """A body that refuses the dispatch because the handover chain has no links left (3.8).

    Substituted for the real body when the successor about to be dispatched is one
    handover past ``policy.max_continuations``. It is a BODY rather than a record built
    on the spot so the refusal is dispatched, journaled and hashed exactly like any other
    node's outcome - which is what makes a replay reach the same end without re-deriving
    the decision, and what gives the run a record to explain the chain's end with.

    Args:
        error: The ``CONTINUATION_LIMIT`` error to stamp.
        spec: The refused successor; names the executor and model the chain was running
            on, so the record does not claim a dispatch that never happened ran nowhere.

    Returns:
        A body that runs nothing and returns the refusal.
    """

    async def refuse(node_dir: Path) -> NodeOutcome:
        """Return the chain-limit refusal; ``node_dir`` is unused, nothing is written."""
        return NodeOutcome(
            status=NodeStatus.FAILED,
            executor_used=spec.executor or "-",
            model_used=spec.model or "-",
            effort_used="-",
            error=error,
        )

    return refuse


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
