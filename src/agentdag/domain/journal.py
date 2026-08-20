"""Journal lines (design 3.1/3.2): the six events slice 1 emits, one JSON object per line.

Contents:
    * :class:`StartedLine`, :class:`ResultLine`, :class:`RunStartedLine`,
      :class:`ResumeLine`, :class:`ApproveDecisionLine`, :class:`RunSummaryLine` -
      the six line shapes slice 1 emits into ``journal.jsonl``.
    * :data:`JournalLine` - the discriminated union of the six.
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
    "JournalLine",
    "ResultLine",
    "ResumeLine",
    "RunStartedLine",
    "RunSummaryLine",
    "StartedLine",
    "dump_journal_line",
    "parse_journal_line",
]

_AT = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
"""ISO-8601 UTC timestamp with an explicit ``+00:00`` offset, produced by the scheduler
(design 3.3, O19) - never a trailing ``Z``, which datetime.fromisoformat rejects before
Python 3.11 and which hides a local-time producer."""


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
    reason: Literal["decision", "crash", "restart", "manual"]
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


JournalLine = Annotated[
    StartedLine | ResultLine | RunStartedLine | ResumeLine | ApproveDecisionLine | RunSummaryLine,
    Field(discriminator="event"),
]
"""The discriminated union of every journal line slice 1 emits, keyed on ``event``."""

_ADAPTER: TypeAdapter[JournalLine] = TypeAdapter(JournalLine)


def parse_journal_line(text: str) -> JournalLine:
    """Parse one JSON object into its typed journal line.

    Args:
        text: One JSON object, as one line of ``journal.jsonl``.

    Returns:
        The typed line matching ``text["event"]``.

    Raises:
        ValueError: ``text`` is not valid JSON, or its ``event`` does not match any
            of the six known lines (pydantic's ``ValidationError`` is a ``ValueError``).

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
