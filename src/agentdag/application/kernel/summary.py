"""The run summary line: design 3.5's drift signals (design 3.5).

:func:`run_summary_line` is the pure half: it builds a
:class:`~agentdag.domain.journal.RunSummaryLine` from values already measured
elsewhere (the records, the journal's size, the coordinator's own counters), and
touches neither the filesystem nor the clock. :func:`append_run_summary` is the
measurement half moved here from ``run.py``: it reads the journal and the run
directory, shapes what it read through the pure function, and appends the result -
these ARE filesystem and clock reads, because gathering a run's own measurements is
what a run summary is, not a violation of the pure function's contract.

Contents:
    * :func:`run_summary_line` - build the :class:`~agentdag.domain.journal.RunSummaryLine`.
    * :func:`append_run_summary` - gather one launch's measurements and append the line.
"""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING

from ...domain.journal import RunSummaryLine
from ...domain.keys import hash8
from .ports import stamp

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...domain.journal import ApproveDecisionLine, ResultLine
    from ...domain.models import ResultRecord
    from .context import Coordinator
    from .ports import RunDir

__all__ = ["append_run_summary", "run_summary_line"]

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
    results: Sequence[ResultLine],
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
        results: Every ``result`` line the run's journal holds, in file order. The LINES,
            not the bare records, because a line carries the dispatch key the brief
            length below is joined on.
        journal_bytes: The journal file's size, measured BEFORE this line is appended.
        journal_lines: How many lines the journal held, likewise before this one.
        replay_seconds: How long folding the replay index took on a relaunch, or
            ``None`` on a run's first start (nothing was replayed).
        human_interactions: How many human decisions the coordinator folded in.
        tokens_by_row: Tokens charged per model row, as the coordinator counted them.
        at: This line's timestamp, already rendered by the caller's one clock reading.
        brief_lengths: Journal key -> the length in characters of the brief THAT dispatch
            ran under, for the overhead estimate below. Keyed by dispatch, not by node,
            so a node dispatched twice under different briefs is measured twice, once
            against each.

    Returns:
        The summary line, ready to append. ``citation_coverage`` is empty: it is a
        synth-node signal and graph A has no synth node, so an empty list is the
        honest reading rather than a zero that would look like a measured miss.

    Example:
        >>> line = run_summary_line(
        ...     run_id="r1", policy_version="sha256:0", results=[], journal_bytes=10,
        ...     journal_lines=2, replay_seconds=None, human_interactions=0,
        ...     tokens_by_row={}, at="2026-08-17T09:12:03+00:00", brief_lengths={},
        ... )
        >>> (line.records_per_node, line.overhead_fraction)
        (0.0, {'median': 0.0, 'p90': 0.0})
    """
    return RunSummaryLine(
        run_id=run_id,
        policy_version=policy_version,
        overhead_fraction=_overhead_fraction(results, brief_lengths),
        citation_coverage=[],
        journal_bytes=journal_bytes,
        replay_seconds=replay_seconds,
        records_per_node=_records_per_node(results),
        tokens_by_row=dict(tokens_by_row),
        journal_lines=journal_lines,
        human_interactions=human_interactions,
        at=at,
    )


def append_run_summary(co: Coordinator, *, replay_seconds: float | None) -> None:
    """Gather this launch's measurements and append the run's summary line.

    Moved here from ``run.py`` (a measurement concern, not a launch-lifecycle one):
    everything read here - the journal, the coordinator's counters, a node's own
    ``brief.md`` - is what makes ``run_summary_line`` a full line rather than a bare
    shaping function with nowhere to get its inputs from.

    Called once per launch that reaches ``done`` (``run.py``'s ``_drive``), which
    means a launch that REPLAYS a finished run to ``done`` again appends ANOTHER
    summary line, not zero: the alternative - writing one only on the launch that
    first reached ``done`` - would need the coordinator to know it is repeating
    itself, which is exactly the kind of state a deterministic replay must not carry.
    Every field here is computed over the run's WHOLE journal as it stands at this
    launch, not just what this launch itself dispatched, so a later summary line is
    still an honest total even though it is not the run's only one.

    Args:
        co: The coordinator whose journal, run directory and counters are read.
        replay_seconds: How long folding the replay index took on a relaunch, or
            ``None`` on a run's first start.
    """
    journal = co.dispatcher.journal
    lines = journal.lines()
    results = [line for line in lines if line.event == "result"]
    journal.append(
        run_summary_line(
            run_id=co.run_id,
            policy_version=co.policy.version,
            results=results,
            journal_bytes=co.run_dir.journal_path.stat().st_size,
            journal_lines=len(lines),
            replay_seconds=replay_seconds,
            human_interactions=_human_interactions(co),
            tokens_by_row=co.tokens_by_row,
            at=stamp(co.clock),
            brief_lengths=_brief_lengths(co.run_dir, results),
        )
    )


def _human_interactions(co: Coordinator) -> int:
    """Count DECISIONS a human made across the run's WHOLE journal, not just this launch.

    Read from the dispatcher's replay index rather than from a per-launch counter: a
    launch's own ``fold_decisions`` call only journals decisions recorded since the
    PREVIOUS launch (a decision already folded is skipped), so a resume that finds
    every decision already folded would report zero however many humans this run
    actually asked. The index is rebuilt from the full journal at construction and
    refreshed by every ``fold_decisions`` call, so by the time this reads it, it holds
    every decision the run has ever recorded.

    That index is keyed by (node id, payload hash), so this counts DECISIONS and not
    nodes: an approve node whose payload changed asks its decider a second question, and
    two answers are two interactions - which is what the field means, "how many times a
    human had to answer something during the run". Keyed by node id alone it read 1.

    Args:
        co: The coordinator whose dispatcher's replay index is read.

    Returns:
        How many of those decisions were not the ``"system"`` sentinel token id.
    """
    return sum(1 for decision in co.dispatcher.index.decisions.values() if _by_a_human(decision))


def _by_a_human(decision: ApproveDecisionLine) -> bool:
    """Return whether a folded decision was made by a person, not the system default."""
    return decision.token_id != "system"  # nosec B105  # noqa: S105 - a token_id VALUE, not a secret


def _brief_lengths(run_dir: RunDir, results: Sequence[ResultLine]) -> dict[str, int]:
    """Map each result's journal KEY to the length of the brief that dispatch ran under.

    Joined by key rather than by node id, because a node dispatched twice has one
    ``brief.md`` per key: attributing either brief to both records would move the
    overhead figure exactly when a node was re-dispatched, which is the drift the
    signal exists to watch. A key names its own directory (``nodes/<node_id>/<hash8(key)>/``),
    so the join is exact.

    Reads through :meth:`~agentdag.application.kernel.ports.RunDir.read_text` rather
    than composing the path from ``run_dir.root`` itself, so this module never assumes
    the on-disk layout only the adapter owns. A missing brief contributes nothing
    rather than raising: the summary must never be the thing that fails a finished run.

    Args:
        run_dir: The run directory a node's brief is read from.
        results: Every ``result`` line the journal holds.

    Returns:
        Journal key -> the length in characters of the brief that dispatch ran under.
    """
    lengths: dict[str, int] = {}
    for line in results:
        rel = f"nodes/{line.record.node_id}/{hash8(line.key)}/brief.md"
        try:
            text = run_dir.read_text(rel)
        except FileNotFoundError:
            continue
        lengths[line.key] = len(text)
    return lengths


def _records_per_node(results: Sequence[ResultLine]) -> float:
    """Return how many records the run wrote per distinct node id.

    A figure above 1.0 means nodes were dispatched more than once - a retry, an
    escalation, or a crash-window re-dispatch - which is the drift design 3.5 watches.

    Args:
        results: Every ``result`` line the journal holds.

    Returns:
        ``len(results) / len(distinct node ids)``, or ``0.0`` when there are none.

    Example:
        >>> _records_per_node([])
        0.0
    """
    if not results:
        return 0.0
    return len(results) / len({line.record.node_id for line in results})


def _overhead_fraction(results: Sequence[ResultLine], brief_lengths: Mapping[str, int]) -> dict[str, float]:
    """Return the median and p90 share of a node's input tokens that was NOT its brief.

    A record qualifies only when it carries a measured first turn
    (``key_facts["first_turn_input_tokens"]``) and a positive ``tokens.in``; anything
    else has nothing to divide. With no qualifying record the answer is two zeros
    rather than an omission, because the field is not optional in the line's schema.

    Args:
        results: Every ``result`` line the journal holds.
        brief_lengths: Journal key -> that dispatch's brief length in characters; a key
            missing here contributes an estimated brief of zero tokens, which reports its
            FULL first turn as overhead.

    Returns:
        ``{"median": ..., "p90": ...}``.

    Example:
        >>> _overhead_fraction([], {})
        {'median': 0.0, 'p90': 0.0}
    """
    fractions = sorted(
        fraction
        for fraction in (_overhead_of(line.record, brief_lengths.get(line.key, 0)) for line in results)
        if fraction is not None
    )
    if not fractions:
        return {"median": 0.0, "p90": 0.0}
    return {"median": float(statistics.median(fractions)), "p90": _nearest_rank(fractions, _P90)}


def _overhead_of(record: ResultRecord, brief_length: int) -> float | None:
    """Return one record's overhead fraction, or ``None`` when it cannot be computed.

    Two ways this deliberately departs from a literal reading of design 3.5, both
    consequences of what a record actually carries rather than choices made here:

    * The denominator is ``tokens.in`` - the WHOLE first turn's input tokens, cache
      reads included - because that is the only per-record token figure the schema
      has; there is no separate "prompt only" count to divide by instead.
    * Only CLAUDE rows ever qualify, by construction rather than by a filter here: a
      code node (``reduce``, ``gate``, ``scan``, ``stage``, ``apply``, ``approve``)
      never reports ``first_turn_input_tokens`` in its ``key_facts``, so every one of
      them returns ``None`` below and the aggregate in :func:`_overhead_fraction`
      is silently a claude-only figure, not a whole-run one.

    Args:
        record: The record to judge.
        brief_length: The length in characters of the brief its node ran under.

    Returns:
        ``max(0, first_turn_input_tokens - brief_length / 4) / tokens.in``, clamped
        to ``1.0`` so a brief-length UNDER-estimate can never report more than "all of
        it was overhead"; or ``None`` when the record carries no first-turn figure or
        no positive input-token count.
    """
    first_turn = record.key_facts.get("first_turn_input_tokens")
    tokens = record.tokens
    # bool is an int subclass, and Tokens.in_ has no lower bound, so both are excluded
    # explicitly rather than left to arithmetic that would silently produce a number.
    if not isinstance(first_turn, int) or isinstance(first_turn, bool):
        return None
    if tokens is None or tokens.in_ is None or tokens.in_ <= 0:
        return None
    return min(1.0, max(0.0, first_turn - brief_length / _CHARS_PER_TOKEN) / tokens.in_)


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
