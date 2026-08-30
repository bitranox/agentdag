"""Journal lines (design 3.1/3.2): every event the run records, one JSON object per line.

Contents:
    * :class:`StartedLine`, :class:`ResultLine`, :class:`RunStartedLine`,
      :class:`ResumeLine`, :class:`ApproveDecisionLine`, :class:`RunSummaryLine` -
      the six line shapes slice 1 emits into ``journal.jsonl``.
    * :class:`CancelRequestedLine`, :class:`CancelLine` - the two M3 adds for
      ``run cancel`` (design 3.4, O25): the intent folded into the journal, and its
      later, VERIFIED outcome once the run's scope is confirmed empty.
    * :class:`RetryGrantLine` - M3's ``run retry``: an operator granting one spent node
      another attempt, folded in from ``retries/<node_id>.<hash8>.json``.
    * :class:`PlanAcceptedLine`, :class:`PlanInvalidatedLine`, :class:`SubtreeDoneLine` -
      M6's planning loop (design 4), emitted by
      :func:`~agentdag.application.kernel.execute.execute_plan`: what a planner dispatch
      produced, and how the subtree that ran it ended.
    * :data:`JournalLine` - the discriminated union of them all.
    * :func:`parse_journal_line` - one JSON object -> the typed line.
    * :func:`dump_journal_line` - the typed line -> one compact, sorted-key JSON line.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .models import ResultRecord

__all__ = [
    "ApproveDecisionLine",
    "CancelLine",
    "CancelRequestedLine",
    "JournalLine",
    "PlanAcceptedLine",
    "PlanInvalidatedLine",
    "ResultLine",
    "ResumeLine",
    "RetryGrantLine",
    "RunStartedLine",
    "RunSummaryLine",
    "StartedLine",
    "SubtreeDoneLine",
    "dump_journal_line",
    "parse_journal_line",
]

_AT = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
"""ISO-8601 UTC timestamp with an explicit ``+00:00`` offset, produced by the scheduler
(design 3.3, O19) - never a trailing ``Z``, which hides a local-time producer."""


class _Line(BaseModel):
    """Base of every journal line: the ``at`` timestamp every event carries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    at: str = _AT


class StartedLine(_Line):
    """A node was dispatched under this journal key (design 3.1)."""

    event: Literal["started"] = "started"
    key: str
    node_id: str
    attempt: int


class ResultLine(_Line):
    """A node's dispatch completed with this record (design 3.1)."""

    event: Literal["result"] = "result"
    key: str
    record: ResultRecord


class RunStartedLine(_Line):
    """The first line of a run: who started it, with what (design 3.4, O20)."""

    event: Literal["run_started"] = "run_started"
    run_id: str
    workflow: str
    args: dict[str, Any]
    by: str
    token_id: str
    policy_version: str


class ResumeLine(_Line):
    """A relaunch of the coordinator against this run dir (design 3.4, O20)."""

    event: Literal["resume"] = "resume"
    run_id: str
    reason: Literal["decision", "crash", "restart", "manual", "retry"]
    by: str
    token_id: str


class ApproveDecisionLine(_Line):
    """A decision folded into the journal from ``decisions/<node_id>.<hash8>.json`` (design 3.4).

    ``payload_hash`` is the other half of the decision's identity: decisions are recorded
    per (``node_id``, ``payload_hash``), so an approval binds to the exact payload a human
    was shown and never carries over to a changed one. It is REQUIRED - there is no
    hash-less decision any more (:class:`~agentdag.domain.models.Decision` refuses to be
    built without one), so every line this kernel folds carries a real hash.
    """

    event: Literal["approve_decision"] = "approve_decision"
    node_id: str
    decision: str
    reason: str
    by: str
    token_id: str
    payload_hash: str = Field(min_length=1)


class CancelRequestedLine(_Line):
    """A cancel INTENT folded into the journal (design 3.1, 3.4, O25; ``journal-line.schema.json``'s
    ``cancel_requested_line``).

    ``run.cancel``/``agentdag run cancel`` never appends this line itself - it writes the
    intent as a file under ``decisions/`` (``_run.cancel.json`` for a whole-run cancel,
    write discipline shared with an approve :class:`~agentdag.domain.models.Decision`) and
    returns at once. Whichever process later holds this run's lock (the still-live
    coordinator, at its next node boundary or lock wait, or a later relaunch that reclaims
    a stale lock) folds the intent into the journal as this line, exactly once, before the
    verified :class:`CancelLine` that follows it.
    """

    event: Literal["cancel_requested"] = "cancel_requested"
    run_id: str
    node_id: str | None
    """The node running when the cancel arrived, or ``None`` for a whole-run cancel."""
    by: str
    token_id: str


class CancelLine(_Line):
    """The VERIFIED end of a cancel: written once the run scope's cgroup is empty (design 3.4).

    ``verified`` is never the stop verb's own return value taken on trust - it is set
    from an actual EMPTY-CGROUP check (:meth:`~agentdag.application.kernel.ports.Scope.kill`
    already polls exactly that), or ``False`` with the reason recorded elsewhere (the CLI's
    own output) when the run's :class:`~agentdag.application.kernel.ports.Scope` cannot
    confirm a kill across two separate process invocations at all (``NoScope``).

    ``node_id`` is required and non-empty per ``journal-line.schema.json``'s
    ``cancel_line``, which carries no per-node/whole-run distinction of its own (unlike
    :class:`CancelRequestedLine`'s nullable ``node_id``); a whole-run cancel uses the same
    ``"_run"`` sentinel :mod:`~agentdag.adapters.kernel.run_store_fs` already reserves for
    its ``_run.cancel.json`` intent file.
    """

    event: Literal["cancel"] = "cancel"
    node_id: str = Field(min_length=1)
    verified: bool


class RetryGrantLine(_Line):
    """An operator granting ONE more attempt to a node whose attempts are spent (``run retry``).

    Folded in from ``retries/<node_id>.<hash8(key)>.json`` by
    :meth:`~agentdag.application.kernel.context.Coordinator.fold_retry_grants`, the way an
    approve :class:`ApproveDecisionLine` is folded from ``decisions/``. Once folded it stays
    in the journal, so every later replay re-makes the same decision in the same order.

    ``key`` is the journal key of the FAILED attempt being granted another go, and it is the
    grant's self-limiting half: the retry is dispatched under ``attempt + 1``, which is an identity
    field, so it produces a DIFFERENT key and this grant can never match twice. That is what
    makes it self-limiting - no counter, no consumed flag, and no way for an unattended run to
    loop on a grant nobody withdrew.

    ``node_id`` is the other half of the match, not decoration. A journal key carries no node id
    (design 3.2's identity table), so two nodes whose work is identical share one key; matching
    the key alone would run the authorised attempt once PER twin, because a freshly granted key
    is one the journal does not hold yet and so nothing serves the retried record to the second.
    The cost of the pair is that a twin keeps the stale failure until it is granted too.
    """

    event: Literal["retry_grant"] = "retry_grant"
    node_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    reason: str
    by: str
    token_id: str


class RunSummaryLine(_Line):
    """The drift signals of design 3.5, written at the end of every launch that reaches ``done``.

    Once per LAUNCH, not once per run: a relaunch that replays a finished run to ``done``
    again appends another one (see
    :func:`~agentdag.application.kernel.summary.append_run_summary` for why writing it
    only the first time would need state a deterministic replay must not carry). Every
    field is computed over the run's WHOLE journal as it stands at that moment, so each
    line is an honest total on its own; a reader wanting the run's final figures takes
    the LAST such line, never the only one.
    """

    event: Literal["run_summary"] = "run_summary"
    run_id: str
    policy_version: str
    overhead_fraction: dict[str, float]
    citation_coverage: list[dict[str, Any]]
    journal_bytes: int
    replay_seconds: float | None
    records_per_node: float
    tokens_by_row: dict[str, int]
    journal_lines: int
    human_interactions: int
    key_collisions: list[dict[str, Any]] = []
    """Every journal key that more than one NODE landed on, as ``{key, node_ids}``.

    A key carries no node id, so two nodes whose work is identical share one. Since the
    2026-08-20 decision each still gets its own record, so a collision is no longer a wrong
    record - but it is a graph its author probably did not mean, and it costs a dispatch, so
    it surfaces here instead of being silent. One node with several records under one key (a
    retry, a crash-window redispatch) is the ordinary shape and is NOT reported."""


class PlanAcceptedLine(_Line):
    """A planner's plan passed the validator and this subtree is about to run it (design 4).

    Emitted for the ROOT plan and for every sub-plan, and for a RE-PLAN as much as a first
    plan, so the journal shows how many times a subtree was planned and with how many
    entries each time. That count is the cheap signal that a re-plan loop is churning: three
    accepted plans for one node id is a subtree that has been re-planned twice.
    """

    event: Literal["plan_accepted"] = "plan_accepted"
    key: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    """The ``plan`` ENTRY's node id - the planner node that produced it, not an entry of the
    accepted plan. A plan's own entries get their ids allocated by the coordinator and appear
    in their own ``started``/``result`` lines."""

    entries: int = Field(ge=0)
    """How many entries the accepted plan carries."""


class PlanInvalidatedLine(_Line):
    """A planner ran and the validator refused what it wrote (design 4).

    The counterpart of :class:`PlanAcceptedLine`, and the one that makes a refusal readable
    after the fact: without it a refused sub-plan shows only as a planner node's record and a
    subtree that never ran, with nothing saying the plan was rejected rather than the planner
    having failed.
    """

    event: Literal["plan_invalidated"] = "plan_invalidated"
    key: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    reasons: tuple[str, ...] = Field(min_length=1)
    """The validator's reasons VERBATIM, every one of them, never flattened into a summary.

    The next planner is briefed with these, and a planner told about the first of four
    mistakes fixes one and is refused again - the same rule
    :class:`~agentdag.application.kernel.planner.NotPlanned` follows for the same reason."""


class SubtreeDoneLine(_Line):
    """One plan's subtree reached terminal, and whether its ``done_when`` settled TRUE.

    ``done`` is the plan's OWN verdict, not "every node succeeded": a subtree whose entries
    all landed but whose ``done_when`` stayed undecided is NOT done, because a completion
    condition that cannot be settled has not been met (the same three-valued rule
    :func:`~agentdag.domain.condition.evaluate` applies everywhere else).
    """

    event: Literal["subtree_done"] = "subtree_done"
    key: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    """The ``plan`` entry whose subtree this was, or the ROOT plan's own planner node."""

    done: bool


JournalLine = Annotated[
    StartedLine
    | ResultLine
    | RunStartedLine
    | ResumeLine
    | ApproveDecisionLine
    | CancelRequestedLine
    | CancelLine
    | RetryGrantLine
    | RunSummaryLine
    | PlanAcceptedLine
    | PlanInvalidatedLine
    | SubtreeDoneLine,
    Field(discriminator="event"),
]
"""The discriminated union of every journal line this kernel emits, keyed on ``event``."""

_ADAPTER: TypeAdapter[JournalLine] = TypeAdapter(JournalLine)


def parse_journal_line(text: str) -> JournalLine:
    """Parse one JSON object into its typed journal line.

    Args:
        text: One JSON object, as one line of ``journal.jsonl``.

    Returns:
        The typed line matching ``text["event"]``.

    Raises:
        ValueError: ``text`` is not valid JSON, or its ``event`` does not match any
            of the known lines (pydantic's ``ValidationError`` is a ``ValueError``).

    Example:
        >>> text = '{"event": "started", "key": "v2:sha256:00", "node_id": "n", "attempt": 0,' \\
        ...        ' "at": "2026-08-17T09:12:03+00:00"}'
        >>> parse_journal_line(text).node_id
        'n'
    """
    return _ADAPTER.validate_json(text)


def dump_journal_line(line: JournalLine) -> str:
    """Render a journal line as one line of compact JSON with sorted keys.

    No trailing newline is included - the journal adapter appends it. Field names
    use their schema alias (``in``, not ``in_``, inside a nested :class:`~agentdag.domain.models.Tokens`).

    Args:
        line: The typed line to render.

    Returns:
        One line of compact, sorted-key JSON.

    Example:
        >>> from agentdag.domain.journal import StartedLine
        >>> line = StartedLine(key="v2:sha256:" + "0" * 64, node_id="n", attempt=0, at="2026-08-17T09:12:03+00:00")
        >>> dump_journal_line(line)
        '{"at":"2026-08-17T09:12:03+00:00","attempt":0,"event":"started","key":"v2:sha256:0000000000000000000000000000000000000000000000000000000000000000","node_id":"n"}'
    """
    # pydantic's dump_json does not sort keys; re-encode through json.dumps for that.
    parsed = json.loads(_ADAPTER.dump_json(line, by_alias=True, exclude_none=False))
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
