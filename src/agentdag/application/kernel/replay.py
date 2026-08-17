"""The replay index: what the journal proves happened, folded without re-running anything.

A resume decision (design 3.2, 3.3) is made against this typed view of the journal's
lines, never against the raw list - folding it once in one place keeps "what is still
in flight" and "what already has a result" defined exactly once.

Contents:
    * :class:`ReplayIndex` - the folded view a resume decision is made against.
    * :func:`build_replay_index` - fold a journal's lines into a :class:`ReplayIndex`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...domain.journal import ApproveDecisionLine, JournalLine, RunStartedLine
    from ...domain.models import ResultRecord

__all__ = ["ReplayIndex", "build_replay_index"]


@dataclass
class ReplayIndex:
    """The folded view a resume decision is made against (design 3.2, 3.3).

    Attributes:
        results: The LATEST result record for every key that has one.
        crash_window: Keys with a ``started`` line and no later ``result`` - the
            candidates for redispatch when the coordinator resumes after a crash.
        decisions: The LATEST approve decision per node id.
        key_sequence: Every ``started`` key, in file order, duplicates included -
            each is a real dispatch attempt, so this is the oracle a replay-purity
            check compares a fresh run's dispatch order against.
        run_started: The run's ``run_started`` line, or ``None`` if the journal has
            not been given one yet.
    """

    results: dict[str, ResultRecord]
    crash_window: set[str]
    decisions: dict[str, ApproveDecisionLine]
    key_sequence: list[str]
    run_started: RunStartedLine | None


def build_replay_index(lines: Sequence[JournalLine]) -> ReplayIndex:
    """Fold ``lines`` into the typed view a resume decision is made against.

    Args:
        lines: A journal's lines, in file order (as :meth:`~agentdag.application.kernel.ports.Journal.lines`
            returns them).

    Returns:
        The folded index: a ``started`` line appends its key to ``key_sequence`` and
        adds it to ``crash_window``; the matching ``result`` moves it into ``results``
        and drops it from ``crash_window``; ``approve_decision`` overwrites the
        node's entry in ``decisions``; ``run_started`` sets ``run_started``.

    Example:
        >>> from agentdag.domain.journal import ResultLine, StartedLine
        >>> from agentdag.domain.models import NodeStatus, ResultRecord
        >>> at = "2026-08-17T09:12:03+00:00"
        >>> key = "v2:sha256:" + "0" * 64
        >>> record = ResultRecord(node_id="a", attempt=0, status=NodeStatus.DONE, executor_used="code",
        ...                       model_used="-", effort_used="-", input_hash="sha256:0", duration_s=0.0)
        >>> idx = build_replay_index([StartedLine(key=key, node_id="a", attempt=0, at=at),
        ...                           ResultLine(key=key, record=record, at=at)])
        >>> idx.crash_window
        set()
        >>> key in idx.results
        True
    """
    results: dict[str, ResultRecord] = {}
    crash_window: set[str] = set()
    decisions: dict[str, ApproveDecisionLine] = {}
    key_sequence: list[str] = []
    run_started: RunStartedLine | None = None

    for line in lines:
        if line.event == "started":
            key_sequence.append(line.key)
            crash_window.add(line.key)
        elif line.event == "result":
            results[line.key] = line.record
            crash_window.discard(line.key)
        elif line.event == "approve_decision":
            decisions[line.node_id] = line
        elif line.event == "run_started":
            run_started = line

    return ReplayIndex(
        results=results,
        crash_window=crash_window,
        decisions=decisions,
        key_sequence=key_sequence,
        run_started=run_started,
    )
