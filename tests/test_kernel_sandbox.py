"""Tests for the ``Sandbox`` port and its ``none`` adapter (Task 19).

``NoSandbox`` is exercised directly (its two methods are pure and need no run directory
at all); the coordinator stamping test builds the same minimal, real-adapter coordinator
``test_kernel_primitives.py`` does for its code primitives - a ``reduce`` node needs no
executor, so nothing here is a fake standing in for a genuinely external edge.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.context import Coordinator
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.application.kernel.ports import ResolvedRow
from agentdag.application.kernel.sandbox import SandboxRequest
from agentdag.domain.models import Kind, NodeOutcome, NodeSpec, NodeStatus

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from agentdag.domain.models import ResultRecord


class _OneRowPolicy:
    """A minimal tier policy: every spec resolves to the sonnet row on the claude executor.

    Never actually consulted by the ``reduce`` dispatch this module tests - a code
    primitive resolves no row - but :class:`Coordinator` still requires a ``Policy`` to
    build, same as ``test_kernel_primitives.py``'s own ``OneRowPolicy``.
    """

    version: str = "sha256:test"
    max_turns: int = 5
    deny_bash: tuple[str, ...] = ()
    tokens_per_row: Mapping[str, int] = {"sonnet": 10}

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        return ResolvedRow(alias="sonnet", executor="claude")


def _coordinator(tmp_path: Path) -> Coordinator:
    """Build a coordinator over a fresh run directory, wired with the real ``NoSandbox``.

    Mirrors ``test_kernel_primitives.py``'s own ``coordinator()`` helper: every port
    except the policy is the real shipped adapter, because a ``reduce`` node needs none
    of them for real work - only :class:`Coordinator`'s constructor requires them.
    """
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "r1")
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    return Coordinator(
        run_id="r1",
        workflow="t",
        args={},
        dispatcher=Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock()),
        run_dir=run_dir,
        clock=UtcClock(),
        executors={},
        gate_port=MakeTestGate(lock=tmp_path / "gate.lock"),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=_OneRowPolicy(),
        sandbox=NoSandbox(),
        parallel=1,
    )


def _done() -> NodeOutcome:
    """Build a minimal DONE outcome a ``reduce`` fold can return."""
    return NodeOutcome(
        status=NodeStatus.DONE, artefact_refs=["ok"], executor_used="code", model_used="-", effort_used="-"
    )


@pytest.mark.os_agnostic
def test_no_sandbox_guarantees_declares_nothing_enforced() -> None:
    guarantees = NoSandbox().guarantees()
    # All three False, stated plainly - the honest declaration a "none" adapter has to
    # make, not a middling "mostly fine" value; this is a CONTROL against a NoSandbox
    # that quietly claimed to contain something it does not.
    assert (guarantees.adapter, guarantees.filesystem, guarantees.network_egress, guarantees.separate_uid) == (
        "none",
        False,
        False,
        False,
    )


@pytest.mark.os_agnostic
def test_no_sandbox_prepare_yields_the_request_unchanged(tmp_path: Path) -> None:
    request = SandboxRequest(
        node_dir=tmp_path / "nodes" / "n" / "00000000",
        worktree=tmp_path / "wt" / "a",
        isolation_root=tmp_path,
        cwd=tmp_path / "wt" / "a",
        env={"PATH": "/usr/bin"},
        network_allow=(),
    )
    with NoSandbox().prepare(request) as prepared:
        # Identity, not just equality: a NO-OP adapter must not even rebuild an
        # equivalent request - there is nothing here for it to compute from.
        assert prepared is request


@pytest.mark.os_agnostic
def test_a_dispatch_through_the_coordinator_stamps_sandbox_none_on_the_record(tmp_path: Path) -> None:
    co = _coordinator(tmp_path)
    spec = NodeSpec(node_id="r_test", kind=Kind.REDUCE, deadline_s=60)

    record: ResultRecord = asyncio.run(co.reduce(spec, fold=_done))

    assert record.sandbox is not None
    assert record.sandbox.adapter == "none"
    assert (record.sandbox.filesystem, record.sandbox.network_egress, record.sandbox.separate_uid) == (
        False,
        False,
        False,
    )
