"""The Sandbox port: what isolation boundary a node's dispatch actually runs under (Task 19).

**Definition only.** This module ships the port and :class:`SandboxRequest`; the only
adapter shipped alongside it is ``adapters.kernel.sandbox_none.NoSandbox``, which enforces
nothing and says so. A container adapter is a later task, deliberately parked - but the
trap a one-adapter port falls into is designing the seam against that ONE adapter, so it
only ever fits that one. :class:`SandboxRequest` is instead shaped against what a
CONTAINER adapter needs to actually isolate a node (a mount list built from ``node_dir``/
``worktree``/``isolation_root``, an env, a network policy), even though nothing here reads
most of those fields yet - see each field's own docstring for why it exists.

:class:`Sandbox` splits into two calls with different lifetimes on purpose. **What a run's
sandbox enforces overall** (:meth:`Sandbox.guarantees`) is a static property of the wired
adapter - one instance per run, so one answer for every node in it - and the coordinator
stamps it onto every dispatched node's :class:`~agentdag.domain.models.ResultRecord`
(:meth:`~agentdag.application.kernel.context.Coordinator._dispatch`), work nodes and code
nodes alike, so a run's own records can answer "was this node contained?" without trusting
the adapter's name alone. **What one node's dispatch needs prepared**
(:meth:`Sandbox.prepare`) is per-call and only a WORK node (the one kind that hands a
request to an :class:`~agentdag.application.kernel.ports.Executor`) has anything for it to
act on; :class:`~agentdag.application.kernel.ports.Executor.run`'s own signature never
changes for this - the coordinator calls :meth:`Sandbox.prepare` and folds whatever it
yields into the SAME :class:`~agentdag.application.kernel.ports.ExecutorRequest` shape the
executor already accepts, so adding a sandbox never means widening every executor.

Contents:
    * :class:`SandboxRequest` - one node's isolation request, shaped for a future
      container adapter.
    * :class:`Sandbox` - the port: a static declaration, and a per-dispatch preparation hook.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ...domain.models import SandboxGuarantees

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping
    from pathlib import Path

__all__ = ["Sandbox", "SandboxGuarantees", "SandboxRequest"]


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    """One node's isolation request - the fields a CONTAINER adapter needs (Task 19).

    ``NoSandbox`` reads none of these (it yields the request unchanged); every field still
    exists because a container adapter cannot be added later without them, and a port that
    has to change SHAPE to gain its second adapter was never a port.
    """

    node_dir: Path
    """The node's own store dir (writable) - a container's per-node bind mount target for
    whatever the executor writes outside the worktree (the transcript, its own home dir)."""

    worktree: Path | None
    """The repo this node may write (writable), or ``None`` for a node with nothing to
    write - the SECOND bind mount a container needs, kept separate from ``node_dir``
    because the two live at different paths under the run root and a container mounts each
    read-write while everything else under ``isolation_root`` stays out of the mount
    namespace entirely."""

    isolation_root: Path
    """The run root; nothing above it is ever mounted - the ceiling a container adapter
    computes every relative mount path against, same as
    :attr:`~agentdag.application.kernel.ports.ExecutorRequest.isolation_root` already is for
    the write-set scan."""

    cwd: Path
    """The executor's working directory - carried through unchanged today
    (:attr:`~agentdag.application.kernel.ports.ExecutorRequest.cwd`); a container adapter
    may rewrite it to the path the SAME tree is mounted at INSIDE the container, which is
    why :meth:`Sandbox.prepare` yields a (possibly different) request rather than being
    handed nothing back at all."""

    env: Mapping[str, str]
    """The node's environment as prepared so far. Unused by ``NoSandbox`` and not yet
    threaded into :class:`~agentdag.application.kernel.ports.ExecutorRequest` (that request
    has no ``env`` field - the executor builds its own from its
    ``adapters.kernel.executor_claude.CredentialSource``, which this port has no visibility
    into as of Task 19); carried on the request anyway because a container adapter needs to
    know what to inject into the container's OWN environment, and a port missing that field
    would need a shape change to grow one later."""

    network_allow: tuple[str, ...]
    """Hosts an egress policy may permit; ``()`` means no policy is expressed. ``NoSandbox``
    enforces no policy regardless of this value (:attr:`SandboxGuarantees.network_egress` is
    always ``False`` for it) - a container adapter is what would actually turn this into a
    firewall rule or an egress proxy allowlist."""


class Sandbox(Protocol):
    """The isolation boundary a node's dispatch runs under: a declaration, and a per-call hook.

    Exactly one :class:`Sandbox` is wired per run (:class:`~agentdag.application.kernel.
    ports.KernelWiring.sandbox`), so :meth:`guarantees` answers for every node the same way
    - there is no per-node sandbox choice in this design, only a per-node PREPARATION step.
    """

    def guarantees(self) -> SandboxGuarantees:
        """Return what this adapter enforces, unconditionally - never per-request.

        Called once per dispatched node, by the coordinator itself
        (:meth:`~agentdag.application.kernel.context.Coordinator._dispatch`), and stamped
        onto that node's :class:`~agentdag.domain.models.ResultRecord` regardless of what
        kind of node it is - a code node (gate, scan, reduce, ...) never calls
        :meth:`prepare` at all, but it still runs inside the SAME run under the SAME
        adapter, so its record carries the same declaration.
        """
        ...

    @contextmanager
    def prepare(self, request: SandboxRequest) -> Generator[SandboxRequest]:
        """Yield the request as the EXECUTOR should use it (paths/env possibly rewritten).

        Only called for a WORK node, around building its
        :class:`~agentdag.application.kernel.ports.ExecutorRequest` - the one kind of node
        that hands a request to an :class:`~agentdag.application.kernel.ports.Executor`. A
        context manager, not a plain function, because a container adapter needs a matching
        TEARDOWN (stop the container, remove its mounts) once the executor is done with the
        request this yielded - the ``with`` block's exit is that hook, even though
        ``NoSandbox`` has nothing to tear down.

        Args:
            request: This node's isolation request, built by the coordinator from what it
                knows at dispatch time.

        Yields:
            The request the executor should actually use - unchanged for a NO-OP adapter
            like ``NoSandbox``, or with ``cwd``/``env``/etc. rewritten to whatever a real
            sandbox prepared (e.g. the same tree's path INSIDE a container).
        """
        ...
