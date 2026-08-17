"""The coordinator kernel's application layer: ports it depends on, and pure replay logic.

Contents:
    * :mod:`.ports` - Protocols for the clock, journal, run lock, executor and scope.
    * :mod:`.replay` - folding a journal's lines into a typed replay index.
"""

from __future__ import annotations

__all__: list[str] = []
