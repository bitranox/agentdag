"""The coordinator kernel's application layer: its ports, and the deterministic program over them.

Contents:
    * :mod:`.ports` - Protocols for the clock, journal, run lock, executor, policy,
      isolation scanner and scope.
    * :mod:`.replay` - folding a journal's lines into a typed replay index.
    * :mod:`.dispatch` - the ONE path every node takes: serve it, or run it and record it.
    * :mod:`.context` - the coordinator a workflow program is handed.
    * :mod:`.workflow_check` - refusing a workflow that reaches for the clock or randomness.
"""

from __future__ import annotations

__all__: list[str] = []
