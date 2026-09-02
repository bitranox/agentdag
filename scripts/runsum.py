"""Summarise a run directory: its journal phases, and what each node actually cost.

A probe tool, not part of the shipped package. It exists because reading a run today
means opening ``journal.jsonl`` and every node transcript by hand: ``agentdag run status``
reports ``tokens_by_row: {}`` for a live run and the end-of-run notification carries no
numbers, so there is nothing that answers "what did that run cost" (``OPEN-WORK.md`` 77).
This answers it well enough to compare two runs; it is deliberately NOT the operator verb
that gap asks for.

Two things it does on purpose, because the obvious version of each is wrong:

* It charges ``input_tokens + cache_creation_input_tokens`` and reports ``cache_read``
  in its own column. Folding the cache read into one total re-counts the whole prefix on
  every turn, so the figure grows with conversation length rather than with work done.
* It counts usage from ``ResultMessage`` only. The CLI repeats one ``message_id`` and its
  usage block once per content block, so summing every event double-counts by about 1.5x.

Usage::

    python scripts/runsum.py <run-dir>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, cast

__all__ = ["main"]

_FIELDS = ("billed_in", "cache_read", "out", "tools", "dispatches")
"""The per-node columns, in the order they are printed."""

_INTERESTING = ("reasons", "rc", "decision", "error", "status")
"""Journal keys worth echoing beside an event, when the event carries one."""


def _journal(run: Path) -> list[dict[str, Any]]:
    """Return every journal line of ``run``, in order."""
    text = (run / "journal.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _span_s(events: list[dict[str, Any]]) -> float:
    """Return the wallclock the journal covers, or 0.0 when it cannot be derived."""
    stamps = [datetime.fromisoformat(str(e["at"])) for e in events if "at" in e]
    return (max(stamps) - min(stamps)).total_seconds() if len(stamps) > 1 else 0.0


def _empty() -> dict[str, int]:
    """Return a zeroed column set, so a node with no model dispatch still prints."""
    return dict.fromkeys(_FIELDS, 0)


def _charge(totals: dict[str, int], usage: dict[str, Any]) -> None:
    """Add one ``ResultMessage`` usage block to ``totals``.

    ``cache_read`` is kept out of ``billed_in`` deliberately - see the module docstring.
    """
    totals["billed_in"] += int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
    totals["cache_read"] += int(usage.get("cache_read_input_tokens", 0))
    totals["out"] += int(usage.get("output_tokens", 0))


def _read_transcript(path: Path, totals: dict[str, int]) -> None:
    """Fold one transcript's tool calls and terminal usage into ``totals``."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        message: dict[str, Any] = json.loads(line)
        if message.get("type") == "AssistantMessage":
            content: list[dict[str, Any]] = message.get("content", [])
            totals["tools"] += sum(1 for block in content if block.get("name"))
        raw = message.get("usage")
        if message.get("type") == "ResultMessage" and isinstance(raw, dict):
            # json.loads hands back Any, so isinstance narrows only to dict[Unknown, Unknown];
            # the cast is where the wire shape is declared, at the boundary that reads it.
            _charge(totals, cast("dict[str, Any]", raw))


def _by_node(run: Path) -> dict[str, dict[str, int]]:
    """Return per-node totals, keyed by node id, summed over that node's dispatches."""
    stats: dict[str, dict[str, int]] = {}
    for transcript in sorted(run.glob("nodes/*/*/transcript.jsonl")):
        node = transcript.parent.parent.name
        totals = stats.setdefault(node, _empty())
        totals["dispatches"] += 1
        _read_transcript(transcript, totals)
    return stats


def _print_phases(events: list[dict[str, Any]]) -> None:
    """Print one line per journal event, with any interesting field it carries."""
    for event in events:
        extra = "".join(f" {key}={str(event[key])[:120]}" for key in _INTERESTING if key in event)
        at = str(event.get("at", ""))[11:19]
        print(f"  {at} {event.get('event')} {event.get('node_id', '')}{extra}")  # noqa: T201 - this IS the report


def _print_costs(stats: dict[str, dict[str, int]]) -> None:
    """Print the per-node cost table and its total row."""
    print(f"  {'node':<14} {'disp':>4} {'billed_in':>10} {'cache_read':>11} {'out':>7} {'tools':>6}")  # noqa: T201
    total = _empty()
    for node, row in sorted(stats.items()):
        print(  # noqa: T201 - this IS the report
            f"  {node:<14} {row['dispatches']:>4} {row['billed_in']:>10} "
            f"{row['cache_read']:>11} {row['out']:>7} {row['tools']:>6}"
        )
        for field in _FIELDS:
            total[field] += row[field]
    print(  # noqa: T201 - this IS the report
        f"  {'TOTAL':<14} {'':>4} {total['billed_in']:>10} "
        f"{total['cache_read']:>11} {total['out']:>7} {total['tools']:>6}"
    )


def main(argv: list[str] | None = None) -> int:
    """Print the phase list and the cost table for the run directory named in ``argv``."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: runsum.py <run-dir>", file=sys.stderr)  # noqa: T201 - diagnostics on stderr
        return 2
    run = Path(args[0])
    events = _journal(run)
    print(f"run: {run.name}")  # noqa: T201 - this IS the report
    print(f"events: {len(events)}  journal span: {_span_s(events):.0f}s")  # noqa: T201
    _print_phases(events)
    _print_costs(_by_node(run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
