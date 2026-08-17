"""Adapters implementing the coordinator kernel's ports (design 3.1, 3.3, 3.4).

Contents:
    * :class:`~.journal_jsonl.JsonlJournal` - the journal port over two JSONL files.
    * :class:`~.lock_file.FileRunLock` - the run lock port over an exclusive-create lock file.
    * :func:`~.lock_file.current_holder` - identify the calling process as a lock holder.
    * :func:`~.lock_file.holder_is_alive` - whether a recorded holder is still the live process.
    * :class:`~.clock_utc.UtcClock` - the clock port over the system's UTC wall clock.
"""

from __future__ import annotations

from .clock_utc import UtcClock
from .journal_jsonl import JsonlJournal
from .lock_file import FileRunLock, current_holder, holder_is_alive

__all__ = ["FileRunLock", "JsonlJournal", "UtcClock", "current_holder", "holder_is_alive"]
