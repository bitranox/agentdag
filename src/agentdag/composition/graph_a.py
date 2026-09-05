"""Production wiring for graph A.

:class:`~agentdag.application.graph_a_ports.GraphAWiring` types its fields as the
PORTS, so assigning the concrete adapters here is itself the conformance check: if an
adapter drifts from its protocol, this module stops type-checking.

Contents:
    * :func:`wire` - build the production wiring for one run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..adapters.graph_a import ClaudeSdkWork, ConsoleApprove, FsRunStore, GitCli, MakeTestGate
from ..application.graph_a_ports import GraphAWiring

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["wire"]


def wire(*, runs: Path) -> GraphAWiring:
    """Build the production wiring, creating a fresh run directory.

    Args:
        runs: Directory holding every run; a new timestamped one is created inside it.

    Returns:
        The wiring for this run.
    """
    return GraphAWiring(
        git=GitCli(),
        # No command, deliberately: the baseline is the CONTROL the kernel is measured against,
        # and a gate an operator can change is a variable in that comparison. `[kernel]
        # gate_command` is the kernel's (`composition/kernel.py`), and does not reach here.
        gate=MakeTestGate(),
        work=ClaudeSdkWork(),
        approve=ConsoleApprove(),
        store=FsRunStore.create(runs),
    )
