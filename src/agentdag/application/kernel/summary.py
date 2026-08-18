"""The run summary line: design 3.5's drift signals, computed as a pure function.

A run's last journal line is what a later reader compares two runs by, so it is built
here from values already measured elsewhere (the records, the journal's size, the
coordinator's own counters) rather than by reading anything itself. Nothing in this
module touches the filesystem or the clock: :mod:`agentdag.application.kernel.run`
gathers the inputs and this function shapes them.

Contents:
    * :func:`run_summary_line` - build the :class:`~agentdag.domain.journal.RunSummaryLine`.
"""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING

from ...domain.journal import RunSummaryLine

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...domain.models import ResultRecord

__all__ = ["run_summary_line"]

_CHARS_PER_TOKEN = 4
"""How many characters of a brief are ESTIMATED to make one input token.

An estimate, not a measurement: the executor reports the first turn's input tokens but
never how many of them were the brief, so the brief's own share is approximated from its
length. It is only ever subtracted, so a wrong estimate moves the overhead signal, never
any dispatch decision."""

_P90 = 0.9
"""The quantile the second overhead figure reports, by nearest rank."""


def run_summary_line(
    *,
    run_id: str,
    policy_version: str,
    records: Sequence[ResultRecord],
    journal_bytes: int,
    journal_lines: int,
    replay_seconds: float | None,
    human_interactions: int,
    tokens_by_row: Mapping[str, int],
    at: str,
    brief_lengths: Mapping[str, int],
) -> RunSummaryLine:
    """Shape a run's measured values into its summary line (design 3.5).

    Args:
        run_id: The run this summary closes.
        policy_version: The content hash of the tier policy table the run ran under.
        records: Every result record the run's journal holds, in file order.
        journal_bytes: The journal file's size, measured BEFORE this line is appended.
        journal_lines: How many lines the journal held, likewise before this one.
        replay_seconds: How long folding the replay index took on a relaunch, or
            ``None`` on a run's first start (nothing was replayed).
        human_interactions: How many human decisions the coordinator folded in.
        tokens_by_row: Tokens charged per model row, as the coordinator counted them.
        at: This line's timestamp, already rendered by the caller's one clock reading.
        brief_lengths: Node id -> the length in characters of the brief that node ran
            under, for the overhead estimate below.

    Returns:
        The summary line, ready to append. ``citation_coverage`` is empty: it is a
        synth-node signal and graph A has no synth node, so an empty list is the
        honest reading rather than a zero that would look like a measured miss.

    Example:
        >>> line = run_summary_line(
        ...     run_id="r1", policy_version="sha256:0", records=[], journal_bytes=10,
        ...     journal_lines=2, replay_seconds=None, human_interactions=0,
        ...     tokens_by_row={}, at="2026-08-17T09:12:03+00:00", brief_lengths={},
        ... )
        >>> (line.records_per_node, line.overhead_fraction)
        (0.0, {'median': 0.0, 'p90': 0.0})
    """
    return RunSummaryLine(
        run_id=run_id,
        policy_version=policy_version,
        overhead_fraction=_overhead_fraction(records, brief_lengths),
        citation_coverage=[],
        journal_bytes=journal_bytes,
        replay_seconds=replay_seconds,
        records_per_node=_records_per_node(records),
        tokens_by_row=dict(tokens_by_row),
        journal_lines=journal_lines,
        human_interactions=human_interactions,
        at=at,
    )


def _records_per_node(records: Sequence[ResultRecord]) -> float:
    """Return how many records the run wrote per distinct node id.

    A figure above 1.0 means nodes were dispatched more than once - a retry, an
    escalation, or a crash-window re-dispatch - which is the drift design 3.5 watches.

    Args:
        records: Every result record the journal holds.

    Returns:
        ``len(records) / len(distinct node ids)``, or ``0.0`` when there are none.

    Example:
        >>> _records_per_node([])
        0.0
    """
    if not records:
        return 0.0
    return len(records) / len({record.node_id for record in records})


def _overhead_fraction(records: Sequence[ResultRecord], brief_lengths: Mapping[str, int]) -> dict[str, float]:
    """Return the median and p90 share of a node's input tokens that was NOT its brief.

    A record qualifies only when it carries a measured first turn
    (``key_facts["first_turn_input_tokens"]``) and a positive ``tokens.in``; anything
    else has nothing to divide. With no qualifying record the answer is two zeros
    rather than an omission, because the field is not optional in the line's schema.

    Args:
        records: Every result record the journal holds.
        brief_lengths: Node id -> its brief's length in characters; a node missing here
            contributes an estimated brief of zero tokens, which reports its FULL first
            turn as overhead.

    Returns:
        ``{"median": ..., "p90": ...}``.

    Example:
        >>> _overhead_fraction([], {})
        {'median': 0.0, 'p90': 0.0}
    """
    fractions = sorted(
        fraction
        for fraction in (_overhead_of(record, brief_lengths.get(record.node_id, 0)) for record in records)
        if fraction is not None
    )
    if not fractions:
        return {"median": 0.0, "p90": 0.0}
    return {"median": float(statistics.median(fractions)), "p90": _nearest_rank(fractions, _P90)}


def _overhead_of(record: ResultRecord, brief_length: int) -> float | None:
    """Return one record's overhead fraction, or ``None`` when it cannot be computed.

    Args:
        record: The record to judge.
        brief_length: The length in characters of the brief its node ran under.

    Returns:
        ``max(0, first_turn_input_tokens - brief_length / 4) / tokens.in``, or ``None``
        when the record carries no first-turn figure or no positive input-token count.
    """
    first_turn = record.key_facts.get("first_turn_input_tokens")
    tokens = record.tokens
    if not isinstance(first_turn, int) or tokens is None or not tokens.in_:
        return None
    return max(0.0, first_turn - brief_length / _CHARS_PER_TOKEN) / tokens.in_


def _nearest_rank(sorted_values: Sequence[float], quantile: float) -> float:
    """Return ``sorted_values``' nearest-rank quantile.

    Nearest rank rather than an interpolating quantile because the sample is a handful
    of nodes: an interpolated value would report a number no node actually produced.

    Args:
        sorted_values: The values, ascending; must not be empty.
        quantile: The quantile to take, between 0 and 1.

    Returns:
        The value at rank ``ceil(quantile * n)``, 1-based.

    Example:
        >>> _nearest_rank([0.1, 0.2, 0.3, 0.4, 0.5], 0.9)
        0.5
        >>> _nearest_rank([0.25], 0.9)
        0.25
    """
    rank = max(1, math.ceil(quantile * len(sorted_values)))
    return sorted_values[rank - 1]
