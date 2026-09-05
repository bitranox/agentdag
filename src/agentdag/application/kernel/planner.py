"""The planner op: dispatch a planner node and turn what it wrote into a validated ``Plan``.

M6 component 3. One function, and everything it can go wrong with is a REPORT rather than a
raise: a planner that wrote nothing, wrote something unparseable, or wrote a plan the validator
refuses all come back as :class:`NotPlanned` carrying reasons and the planner's own record. The
caller branches on those - a nested plan's reasons become a failed record its parent reads, and
the root's become the cause the next planner is briefed with.

Contents:
    * :data:`PLANNER_PROMPT` - what a planner node is told, including which ops exist.
    * :class:`Planned` / :class:`NotPlanned` - the two outcomes, both carrying the record.
    * :func:`dispatch_planner` - dispatch, read back, parse, validate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ...domain.journal import PlanAcceptedLine, PlanInvalidatedLine
from ...domain.plan import PLAN_FILENAME, Plan, plan_json_schema
from .plan_validate import Accepted, validate_plan
from .ports import stamp

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ...domain.models import NodeSpec, ResultRecord
    from ...domain.policy import RunLimits
    from .registry import OpRegistry, OpSpec, PlanContext

__all__ = ["PLANNER_PROMPT", "NotPlanned", "Planned", "dispatch_planner"]


PLANNER_PROMPT = """You are a planner. Produce ONE plan for the goal in your brief and write it \
as JSON to the file named plan.json in your node directory. Write nothing else.

Your plan must validate against this schema:

{schema}

Every entry's "op" must be one of these registered ops. "does" says what running it means \
in THIS run; "args" lists what that op's "args" object takes; "emits" lists the key_facts \
field names a condition may name for an entry using it, and there are no others:

{ops}

Everything you need to write the plan is in this prompt and in your brief. Do not search \
the filesystem for the ops, their fields or the schema - they are above, and a plan is the \
only thing you are asked to produce.

Notes that will save you a refused plan:

- Node ids are allocated by the coordinator. Whatever you put in an entry's spec.node_id is \
overwritten, so refer to entries by the ids the schema's own examples use.
- "done_when" is required, and a condition may only reference fields the named entry's op \
declares in its output contract.
- A root plan's "done_when" is refused if it would already be true of a run that did \
NOTHING. A gate that is green before the work is green after, a clean scan reads the same \
either way, and a count of what passed is 0 with nothing dispatched - so "rc == 0", \
"stray == 0" and "count == 0" all describe a run that never started. Conjoin something that \
can only be true once work happened: a "work" entry's own field, an "approve" decision, or a \
positive count such as "count >= 1".
- Judging is not yet available: there is no "judge" op to name, and a plan that names one is \
refused. State what would have to be judged in the entry's brief instead.
- The plan is accepted whole or refused whole, with every reason at once.
"""
"""What a planner node is told. Formatted with ``schema`` and ``ops``.

The ops list is not decoration. Refusal by absence is the BACKSTOP - a plan naming something
nobody registered is refused before any spend - but telling the planner up front is the cheap
half, and the difference between a refusal that reads as a rule and one that reads as a typo.
"""


@dataclass(frozen=True, slots=True)
class Planned:
    """A planner node produced a plan the validator accepted."""

    plan: Plan
    """Already through :func:`~agentdag.application.kernel.plan_validate.validate_plan`, with
    node ids ALLOCATED by the coordinator rather than taken from the model."""

    record: ResultRecord
    """The planner node's own record, for the journal."""


@dataclass(frozen=True, slots=True)
class NotPlanned:
    """A planner node ran and no plan came of it. Not an error - a result to branch on."""

    reasons: tuple[str, ...]
    """Why, verbatim: the validator's own reasons when it refused, else what went wrong
    reading or parsing what the node wrote. Never flattened into one summary - the next
    planner is briefed with these."""

    record: ResultRecord
    """The planner node's own record. Present on this branch too, so a run that failed to
    plan still has journal evidence that a planner was dispatched and what it spent."""


async def dispatch_planner(
    *,
    spec: NodeSpec,
    goal: str,
    evidence: Mapping[str, ResultRecord],
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    graph: Mapping[str, NodeSpec],
    is_root: bool,
    allocate_id: Callable[[], str],
    is_stopping: Callable[[], bool] | None = None,
) -> Planned | NotPlanned:
    """Dispatch one planner node and turn what it wrote into a validated :class:`Plan`.

    Args:
        spec: The planner node's spec.
        goal: What this plan is for; the first line of the node's brief.
        evidence: Records the planner should plan against, by node id.
        ctx: The coordinator to dispatch through and the directory to run in.
        registry: The ops a plan may name; also what the prompt advertises.
        limits: Run ceilings the validator enforces.
        graph: Nodes already admitted, which an entry's deps may name.
        is_root: Whether this is the run's own top-level plan (decision 4's rule is a ROOT
            rule, so it is the validator's business, not this function's).
        allocate_id: Hands out the node ids the accepted plan's entries carry.
        is_stopping: Whether this planner's own subtree has asked it to hand over, forwarded
            to the executor's turn seam. ``None`` outside a subtree that can stop.

    Returns:
        :class:`Planned` when the plan validated, else :class:`NotPlanned` with reasons.
    """
    outcome = await _plan_or_reasons(
        spec=spec,
        goal=goal,
        evidence=evidence,
        ctx=ctx,
        registry=registry,
        limits=limits,
        graph=graph,
        is_root=is_root,
        allocate_id=allocate_id,
        is_stopping=is_stopping,
    )
    _journal(outcome, node_id=spec.node_id, ctx=ctx)
    return outcome


async def _plan_or_reasons(
    *,
    spec: NodeSpec,
    goal: str,
    evidence: Mapping[str, ResultRecord],
    ctx: PlanContext,
    registry: OpRegistry,
    limits: RunLimits,
    graph: Mapping[str, NodeSpec],
    is_root: bool,
    allocate_id: Callable[[], str],
    is_stopping: Callable[[], bool] | None,
) -> Planned | NotPlanned:
    """Dispatch the node and turn what it wrote into a plan or into reasons.

    Split from :func:`dispatch_planner` only so the journal line has ONE place to be written
    from: three early returns cannot each remember to write it.
    """
    prompt = PLANNER_PROMPT.format(schema=_schema_text(), ops=_ops_text(registry))
    record = await ctx.co.plan_node(
        spec, brief=_brief(goal, evidence), cwd=ctx.cwd, prompt=prompt, is_stopping=is_stopping
    )
    rel = next((ref for ref in record.artefact_refs if ref.endswith(PLAN_FILENAME)), None)
    if rel is None:
        return NotPlanned(reasons=(f"the planner node wrote no {PLAN_FILENAME}",), record=record)
    try:
        plan = Plan.model_validate_json(ctx.co.run_dir.read_text(rel))
    except ValidationError as exc:
        return NotPlanned(reasons=_parse_reasons(exc), record=record)
    outcome = validate_plan(
        plan, registry=registry, graph=graph, limits=limits, is_root=is_root, allocate_id=allocate_id
    )
    if isinstance(outcome, Accepted):
        return Planned(plan=outcome.plan, record=record)
    return NotPlanned(reasons=outcome.reasons, record=record)


def _journal(outcome: Planned | NotPlanned, *, node_id: str, ctx: PlanContext) -> None:
    """Record what this planner dispatch produced: the plan it got accepted, or the refusal.

    Written HERE rather than by the caller, and both branches at ONE site, because the shape
    worth closing is "a planner ran and nothing said what came of it": a refused plan
    otherwise appears in the journal as a DONE planner record beside a subtree that never
    ran, with nothing distinguishing a rejected PLAN from a planner that fell over. Three
    call sites each remembering to write it is three chances to forget, and forgetting is
    silent - the run still works, the journal just stops explaining itself.

    The key is the planner dispatch's OWN journal key, read off the record it produced -
    ``ResultRecord.input_hash`` is that key, not one of its ingredients
    (``result-record.schema.json``) - so the line joins to that node's ``started`` and
    ``result`` lines instead of standing alone. ``node_id`` is the planner node, never an
    entry of the accepted plan: those get coordinator-allocated ids and lines of their own.
    """
    at = stamp(ctx.co.clock)
    if isinstance(outcome, Planned):
        line: PlanAcceptedLine | PlanInvalidatedLine = PlanAcceptedLine(
            key=outcome.record.input_hash, node_id=node_id, entries=len(outcome.plan.entries), at=at
        )
    else:
        line = PlanInvalidatedLine(key=outcome.record.input_hash, node_id=node_id, reasons=outcome.reasons, at=at)
    ctx.co.dispatcher.journal.append(line)


def _schema_text() -> str:
    """Render the plan schema for the prompt."""
    import json  # noqa: PLC0415 - only the prompt needs it; keeps the module's import surface small

    return json.dumps(plan_json_schema(), indent=2, sort_keys=True)


def _ops_text(registry: OpRegistry) -> str:
    """Render each registered op with the args it takes and the fields it may emit.

    Names alone are not enough, and the gap is not cosmetic. The prompt tells the planner
    that a condition may only name fields the entry's op declares in its output contract,
    so a prompt that withholds those fields states a rule the planner cannot read - and a
    planner holding Bash goes and looks for them. Measured on the first real ``plan-goal``
    run, 2026-09-02: it spent its whole first six minutes grepping the filesystem, out of
    its worktree and into unrelated projects, for exactly the contracts this now prints.
    """
    return "\n".join(_one_op(registry.get(name)) for name in sorted(registry.names()))


def _one_op(op: OpSpec) -> str:
    """Render one op as its name, what it does, its args field names, and its output contract.

    The description carries what neither of the other two lines can: what running the op
    MEANS here. ``gate:make-test`` is the case that forced it - the command it runs is
    configuration (``[kernel] gate_command``), so its name says nothing about what a plan
    naming it will actually execute, and a planner that has to guess plans around a guess.
    """
    args = sorted(op.args_model.model_fields)
    emits = sorted(op.output_contract)
    return (
        f"- {op.name}\n"
        f"    does: {op.description}\n"
        f"    args: {', '.join(args) if args else '(none)'}\n"
        f"    emits: {', '.join(emits) if emits else '(nothing a condition can name)'}"
    )


def _brief(goal: str, evidence: Mapping[str, ResultRecord]) -> str:
    """Compose the planner node's brief from its goal and the records it plans against."""
    lines = [f"Goal: {goal}"]
    if evidence:
        lines.append("")
        lines.append("Evidence, by node id:")
        lines.extend(
            f"- {node_id}: {record.status.value} {record.key_facts}" for node_id, record in sorted(evidence.items())
        )
    return "\n".join(lines)


def _parse_reasons(exc: ValidationError) -> tuple[str, ...]:
    """Turn a pydantic failure into one reason per problem, keeping the field path.

    One per problem rather than one summary, for the same reason the validator returns every
    reason at once: a planner told about the first of four mistakes fixes one and is refused
    again.
    """
    return tuple(
        f"could not parse {PLAN_FILENAME} at {'.'.join(str(part) for part in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors()
    )
