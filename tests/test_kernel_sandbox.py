"""Tests for the ``Sandbox`` port and its ``none`` adapter (Task 19).

``NoSandbox`` is exercised directly (its two methods are pure and need no run directory
at all); the coordinator stamping test builds the same minimal, real-adapter coordinator
``test_kernel_primitives.py`` does for its code primitives - a ``reduce`` node needs no
executor, so nothing here is a fake standing in for a genuinely external edge.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
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
from agentdag.domain.journal import ResultLine
from agentdag.domain.models import Kind, NodeOutcome, NodeSpec, NodeStatus, SandboxGuarantees

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping
    from pathlib import Path

    from agentdag.application.kernel.sandbox import Sandbox
    from agentdag.domain.models import ResultRecord


class _OneRowPolicy:
    """A minimal tier policy: every spec resolves to the sonnet row on the claude executor.

    Never actually consulted by the ``reduce`` dispatch this module tests - a code
    primitive resolves no row - but :class:`Coordinator` still requires a ``Policy`` to
    build, same as ``test_kernel_primitives.py``'s own ``OneRowPolicy``.
    """

    version: str = "sha256:test"
    max_turns: int = 5
    max_attempts: int = 1  # these tests assert one dispatch per node
    deny_bash: tuple[str, ...] = ()
    tokens_per_row: Mapping[str, int] = {"sonnet": 10}
    deadline_ceiling_s: float = 999_999.0

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve any spec to the one row this policy has."""
        return ResolvedRow(alias="sonnet", executor="claude", handover_at_tokens=100_000)


class _FakeSandbox:
    """A ``Sandbox`` distinct from ``NoSandbox``, for the served-record regression test.

    Its declaration (``adapter="fake"``, ``filesystem=True``) exists only so a SERVED
    record's own declaration can be told apart from what THIS launch's differently-wired
    adapter would stamp on a fresh dispatch - proving a served record keeps its own rather
    than getting re-stamped with whatever sandbox happens to be wired on replay.
    """

    def guarantees(self) -> SandboxGuarantees:
        """Return a declaration a real container-ish adapter might make - never ``NoSandbox``'s."""
        return SandboxGuarantees(adapter="fake", filesystem=True, network_egress=False, separate_uid=False)

    @contextmanager
    def prepare(self, request: SandboxRequest) -> Generator[SandboxRequest]:
        """Yield ``request`` unchanged - never actually called by a ``reduce`` node."""
        yield request


def _coordinator(
    tmp_path: Path, *, sandbox: Sandbox | None = None, rd: FsRunDir | None = None
) -> tuple[Coordinator, FsRunDir]:
    """Build a coordinator wired with ``sandbox`` (``NoSandbox`` by default), over ``rd`` or a fresh run dir.

    Mirrors ``test_kernel_primitives.py``'s own ``coordinator()`` helper: every port
    except the policy is the real shipped adapter, because a ``reduce`` node needs none
    of them for real work - only :class:`Coordinator`'s constructor requires them.
    ``rd``, when given, is an EXISTING run directory - what a relaunch does, so a second
    coordinator built over it sees the same journal (and can be wired with a different
    ``sandbox``) as the first.
    """
    if rd is not None:
        run_dir = rd
    else:
        base = tmp_path / "runs"
        base.mkdir(parents=True, exist_ok=True)
        run_dir = FsRunDir.create(base, "r1")
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    co = Coordinator(
        run_id="r1",
        workflow="t",
        args={},
        dispatcher=Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock()),
        run_dir=run_dir,
        clock=UtcClock(),
        executors={},
        gate_port=MakeTestGate(),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=_OneRowPolicy(),
        sandbox=sandbox if sandbox is not None else NoSandbox(),
        parallel=1,
    )
    return co, run_dir


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
    co, _run_dir = _coordinator(tmp_path)
    spec = NodeSpec(node_id="r_test", kind=Kind.REDUCE, deadline_s=60)

    record: ResultRecord = asyncio.run(co.reduce(spec, fold=_done))

    assert record.sandbox is not None
    assert record.sandbox.adapter == "none"
    assert (record.sandbox.filesystem, record.sandbox.network_egress, record.sandbox.separate_uid) == (
        False,
        False,
        False,
    )


@pytest.mark.os_agnostic
def test_a_freshly_dispatched_node_persists_its_sandbox_declaration(tmp_path: Path) -> None:
    """Defect 1's regression test: the field must reach ``record.json`` AND the journal.

    Reading it off the record :meth:`~agentdag.application.kernel.context.Coordinator.reduce`
    returns is not enough - that object existing is compatible with a stamp applied only to
    the RETURNED copy, after everything persisted was already written (the bug this fixes).
    So this asserts against what actually landed on disk: ``record.json`` under the node's
    own directory, and the journal's ``result`` line for the same key.
    """
    co, run_dir = _coordinator(tmp_path)
    spec = NodeSpec(node_id="r_test", kind=Kind.REDUCE, deadline_s=60)

    record: ResultRecord = asyncio.run(co.reduce(spec, fold=_done))

    persisted_path = next(run_dir.root.glob(f"nodes/{spec.node_id}/*/record.json"))
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted.get("sandbox") is not None  # omitted entirely (the pre-fix bug) reads as None here
    assert persisted["sandbox"]["adapter"] == "none"
    assert (persisted["sandbox"]["filesystem"], persisted["sandbox"]["network_egress"]) == (False, False)

    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    result_lines = [line for line in journal.lines() if isinstance(line, ResultLine) and line.key == record.input_hash]
    assert len(result_lines) == 1
    journaled_sandbox = result_lines[0].record.sandbox
    assert journaled_sandbox is not None
    assert journaled_sandbox.adapter == "none"


@pytest.mark.os_agnostic
def test_a_served_record_keeps_its_own_sandbox_declaration_under_a_different_wired_sandbox(tmp_path: Path) -> None:
    """Defect 2's regression test: a replay must not be re-stamped with THIS launch's sandbox.

    The first coordinator dispatches under the real ``NoSandbox``; the journal now holds a
    ``result`` line declaring ``adapter="none"``. A SECOND coordinator, over the SAME run
    directory but wired with ``_FakeSandbox`` (``adapter="fake"``, ``filesystem=True``),
    dispatches the identical spec/brief/input - so the dispatcher SERVES the first launch's
    record rather than running the fold again. That served record must still say
    ``adapter="none"``: what it carries is what it was actually run under, not whatever
    adapter happens to be wired on the launch that replays it.
    """
    co1, run_dir = _coordinator(tmp_path)
    spec = NodeSpec(node_id="r_test", kind=Kind.REDUCE, deadline_s=60)
    first = asyncio.run(co1.reduce(spec, fold=_done))
    assert first.sandbox is not None
    assert first.sandbox.adapter == "none"

    co2, _run_dir_again = _coordinator(tmp_path, sandbox=_FakeSandbox(), rd=run_dir)
    served = asyncio.run(co2.reduce(spec, fold=_done))

    assert served.node_id == first.node_id
    assert served.sandbox is not None
    assert served.sandbox.adapter == "none"  # NOT "fake" - defect 2 is this line
    assert served.sandbox.filesystem is False
