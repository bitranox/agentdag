"""The journal on disk: one writer, O_APPEND, one JSON object per line (design 3.1).

Every call to :meth:`JsonlJournal.append` writes the same line to two files - the
journal itself and an audit copy - so the audit log is always a byte-for-byte
superset of the journal and can never fall behind it.

Contents:
    * :class:`JsonlJournal` - the :class:`~agentdag.application.kernel.ports.Journal` port over two JSONL files.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ...domain.journal import dump_journal_line, parse_journal_line

if TYPE_CHECKING:
    from pathlib import Path

    from ...domain.journal import JournalLine

__all__ = ["JsonlJournal"]

_OWNER_ONLY = 0o600


class JsonlJournal:
    """Journal port over two append-only JSONL files: the journal and its audit copy."""

    def __init__(self, journal_path: Path, audit_path: Path) -> None:
        """Bind the journal to its two files; neither needs to exist yet.

        Args:
            journal_path: The file :meth:`lines` reads back and replay is built from.
            audit_path: The audit copy; every line written to ``journal_path`` is
                also written here, in the same call.
        """
        self._journal = journal_path
        self._audit = audit_path

    def append(self, line: JournalLine) -> None:
        """Append ``line`` to the journal and the audit copy, each fsynced in turn.

        Each file is opened, written, flushed and fsynced independently under
        ``O_WRONLY | O_CREAT | O_APPEND`` - append mode is what makes a single
        writer's lines land whole even if another process opens the same file at
        the same time; this adapter does not itself enforce single-writer, the
        run lock does (design 3.1, 3.4).

        Args:
            line: The typed line to record.
        """
        text = dump_journal_line(line) + "\n"
        for path in (self._journal, self._audit):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _OWNER_ONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

    def lines(self) -> list[JournalLine]:
        """Read the journal (not the audit copy) back as typed lines, in file order.

        Returns:
            Every non-blank line, parsed; an empty list if the journal does not
            exist yet.

        Raises:
            ValueError: a line is not valid JSON or not one of the six known
                events (a torn write from a crash mid-append) - reported with its
                1-based line number rather than silently dropped.
        """
        if not self._journal.exists():
            return []
        parsed: list[JournalLine] = []
        raw_lines = self._journal.read_text(encoding="utf-8").splitlines()
        for number, raw in enumerate(raw_lines, start=1):
            if not raw.strip():
                continue
            try:
                parsed.append(parse_journal_line(raw))
            except ValueError as exc:
                raise ValueError(f"journal line {number} is unreadable: {exc}") from exc
        return parsed
