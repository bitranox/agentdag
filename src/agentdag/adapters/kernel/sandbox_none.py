"""NoSandbox: the honest no-op ``Sandbox`` adapter - today's behaviour, declared as such (Task 19).

Every kernel node before this task already ran exactly this way: the same operating-system
user as the coordinator, no mount boundary, no egress policy. :class:`NoSandbox` changes
nothing about that; it only makes the fact a typed, journaled declaration
(:class:`~agentdag.domain.models.SandboxGuarantees`) instead of something only the README's
"what the kernel does not enforce" section says in prose.

Contents:
    * :class:`NoSandbox` - the port implementation.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from ...domain.models import SandboxGuarantees

if TYPE_CHECKING:
    from collections.abc import Generator

    from ...application.kernel.sandbox import SandboxRequest

__all__ = ["NoSandbox"]

_GUARANTEES = SandboxGuarantees(adapter="none", filesystem=False, network_egress=False, separate_uid=False)
"""Fixed and shared: every :class:`NoSandbox` instance declares the identical, honest
nothing - there is no per-instance configuration that could make one enforce more than
another, so a single frozen value read by every call is exact, not a needless allocation."""


class NoSandbox:
    """The ``Sandbox`` port over no isolation at all: today's behaviour, named honestly.

    ``filesystem``, ``network_egress`` and ``separate_uid`` are all ``False`` - stated
    plainly rather than a middling "partial" value, because a node under this adapter really
    can reach any path the coordinator's own operating-system user can reach and really can
    make any outbound request that user can make (the README's "what the kernel does not
    enforce" section, unchanged in kind by this task).
    """

    def guarantees(self) -> SandboxGuarantees:
        """Return the fixed, all-``False`` declaration - see :data:`_GUARANTEES`."""
        return _GUARANTEES

    @contextmanager
    def prepare(self, request: SandboxRequest) -> Generator[SandboxRequest]:
        """Yield ``request`` completely unchanged: there is nothing here to rewrite or tear down."""
        yield request
