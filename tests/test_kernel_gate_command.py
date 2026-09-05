"""The gate runs a CONFIGURED command, and what is recorded is what actually ran.

The gate used to be hard-wired: ``_build_gate_make_test`` passed the literal ``("make",
"test")`` as ``argv`` while the port ran whatever it had been built with, so the journal key,
the ``input.json`` and the node's brief could all name a command the machine never ran. The
port now REPORTS its command and the coordinator reads it, which makes the recorded argv the
executed argv by construction rather than by two call sites agreeing.

Every arm drives the real adapters - a real ``MakeTestGate`` over a real subprocess, the real
registry, the real ``wire_kernel`` - and the planner arm drives the real ``dispatch_planner``
against a fake executor that records the prompt it was handed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import tomllib
from kernel_fakes import (
    FakeScanner,
    PlanWritingExecutor,
    RecordingExecutor,
    fresh_run_dir,
    outcome,
    policy_path,
    wire,
)
from pydantic import ValidationError
from test_kernel_planner import LIMITS, ids, planner_spec

from agentdag.adapters.graph_a.gate_make import DEFAULT_GATE_COMMAND, GATE_ENV_ALLOWLIST, MakeTestGate
from agentdag.adapters.kernel.executor_claude import OAuthTokenFile
from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.application.kernel.planner import dispatch_planner
from agentdag.application.kernel.registry import PlanContext
from agentdag.composition.kernel import build_op_registry, wire_kernel
from agentdag.domain.models import Budget, Isolation, Kind, NodeSpec, NodeStatus, RunSettings

if TYPE_CHECKING:
    from agentdag.adapters.kernel.run_store_fs import FsRunDir
    from agentdag.application.kernel.context import Coordinator
    from agentdag.domain.models import ResultRecord

_GREEN = (sys.executable, "-c", "raise SystemExit(0)")
"""A green gate command that is NOT the default, so an arm asserting on it cannot pass by
coincidence against a coordinator that still hard-codes ``make test``."""

_RED = (sys.executable, "-c", "raise SystemExit(1)")
"""Its red twin, for the arm about two gates keying differently."""


def gate_spec(node_id: str = "g_test@1") -> NodeSpec:
    """The gate node these arms dispatch."""
    return NodeSpec(
        node_id=node_id,
        kind=Kind.GATE,
        executor="code",
        isolation=Isolation.NONE,
        deadline_s=60,
        budget=Budget(),
    )


def gated(run_dir: FsRunDir, gate_port: MakeTestGate) -> Coordinator:
    """Wire a coordinator whose gate port is this arm's own, everything else as ``wire`` builds it."""
    return wire(run_dir, RecordingExecutor(outcome({})), FakeScanner(), gate_port=gate_port)


def recorded_input(root: Path, record: ResultRecord) -> dict[str, object]:
    """The ``input.json`` the gate dispatch wrote, read off the node dir its log names."""
    node_dir = (root / record.artefact_refs[0]).parent
    return json.loads((node_dir / "input.json").read_text(encoding="utf-8"))


@pytest.mark.os_agnostic
def test_the_gate_port_reports_the_command_it_was_built_with() -> None:
    """A caller that must record what ran asks the port, instead of re-declaring the command."""
    assert MakeTestGate(command=("true",)).command == ("true",)
    assert MakeTestGate().command == DEFAULT_GATE_COMMAND == ("make", "test")


@pytest.mark.os_agnostic
def test_a_gate_built_with_an_empty_command_is_refused() -> None:
    """There is no runnable empty argv, so the port refuses one rather than failing at run time."""
    with pytest.raises(ValueError, match="empty"):
        MakeTestGate(command=())


@pytest.mark.os_agnostic
def test_the_allowlist_carries_the_two_names_a_python_gate_needs() -> None:
    """A gate command that is not ``make test`` still has to be able to start.

    ``PYTHONPATH`` is how a gate reaches a package that is not installed into its own
    interpreter, and ``UV_CACHE_DIR`` is where uv keeps what it would otherwise re-download.
    An allowlist that does not name them drops them, and the failure then surfaces inside the
    project's own tooling with no mention of the gate environment.

    Only ``PYTHONPATH`` is new; ``UV_CACHE_DIR`` was already allowlisted for bmk. Both are
    asserted because the pair is what a gate command that is an interpreter needs, and an
    allowlist entry deleted as unused is exactly how the other half would go.
    """
    assert {"PYTHONPATH", "UV_CACHE_DIR"} <= set(GATE_ENV_ALLOWLIST)


@pytest.mark.os_agnostic
def test_the_gate_node_records_the_command_the_port_actually_ran(tmp_path: Path) -> None:
    """``input.json`` names the port's own command, not the ``make test`` that used to be passed."""
    run_dir = fresh_run_dir(tmp_path)

    record = asyncio.run(gated(run_dir, MakeTestGate(command=_GREEN)).gate(gate_spec(), cwd=run_dir.root))

    assert record.status is NodeStatus.DONE
    assert recorded_input(run_dir.root, record)["argv"] == list(_GREEN)
    assert (run_dir.root / record.artefact_refs[0]).exists()


@pytest.mark.os_agnostic
def test_an_unconfigured_gate_records_the_argv_every_run_before_this_key_recorded(tmp_path: Path) -> None:
    """The replay-compatibility invariant, asserted by VALUE rather than left to inspection.

    A gate node's argv is part of its journal key, and before this key existed every gate node
    recorded ``["make", "test"]``. A default gate must still record exactly that, or every run
    started before the change re-keys and re-dispatches its gates on the next resume, with
    nothing red to say so.
    """
    run_dir = fresh_run_dir(tmp_path)

    record = asyncio.run(gated(run_dir, MakeTestGate()).gate(gate_spec(), cwd=run_dir.root))

    assert recorded_input(run_dir.root, record)["argv"] == ["make", "test"]


@pytest.mark.os_agnostic
def test_the_packaged_config_ships_the_command_an_unconfigured_gate_runs() -> None:
    """The two declarations of the default cannot drift apart unnoticed.

    ``DEFAULT_GATE_COMMAND`` is what a gate built without a command runs; the packaged
    ``[kernel] gate_command`` is what the CLI falls back to. They are the same fact written
    twice, in two languages, and only this compares them.
    """
    path = Path(str(files("agentdag.adapters.config") / "defaultconfig.d" / "60-kernel.toml"))
    with path.open("rb") as handle:
        packaged = tomllib.load(handle)["kernel"]["gate_command"]

    assert tuple(packaged) == DEFAULT_GATE_COMMAND


@pytest.mark.os_agnostic
def test_two_gates_differing_only_in_their_command_are_different_dispatches(tmp_path: Path) -> None:
    """The command is part of the key, so the same node under a new gate is not served a replay.

    The two statuses are the control: equal keys would serve the second dispatch the first's
    record without running anything, and both would read ``done``.
    """
    run_dir = fresh_run_dir(tmp_path)

    first = asyncio.run(gated(run_dir, MakeTestGate(command=_GREEN)).gate(gate_spec(), cwd=run_dir.root))
    second = asyncio.run(gated(run_dir, MakeTestGate(command=_RED)).gate(gate_spec(), cwd=run_dir.root))

    assert recorded_input(run_dir.root, first)["argv"] == list(_GREEN)
    assert recorded_input(run_dir.root, second)["argv"] == list(_RED)
    assert first.input_hash != second.input_hash
    assert (first.status, second.status) == (NodeStatus.DONE, NodeStatus.FAILED)


@pytest.mark.os_agnostic
def test_a_run_carrying_an_empty_gate_command_is_refused_when_its_state_is_read() -> None:
    """A hand-edited ``state.json`` is refused where the run is LOADED, not deep in the wiring.

    ``RunSettings`` is the on-disk record, so it is the boundary: without a length on the field
    the empty command reaches ``MakeTestGate`` and surfaces as a bare ``ValueError`` traceback
    from a relaunch, where every other bad value in that block is refused by name.
    """
    with pytest.raises(ValidationError):
        RunSettings(
            policy_path="p",
            parallel=1,
            max_turns=1,
            default_node_tokens=1,
            deny_bash=(),
            deny_tools=(),
            notify="none",
            credential_file="",
            gate_command=(),
        )


@pytest.mark.os_agnostic
def test_the_registry_describes_the_gate_by_the_command_it_will_run() -> None:
    """The planner is told what the gate runs, and the default is not smuggled in beside it."""
    described = build_op_registry(gate_command=("pytest", "-q")).get("gate:make-test").description

    assert "pytest -q" in described
    assert "make test" not in described


@pytest.mark.os_agnostic
def test_every_registered_op_says_something_about_itself() -> None:
    """A new op cannot reach the planner prompt nameless: the field is required, and this pins
    that none was left as a placeholder short enough to say nothing."""
    registry = build_op_registry()
    said = {name: registry.get(name).description for name in registry.names()}
    thin = {name: text for name, text in said.items() if len(text) < 20}

    assert not thin, f"these ops say nothing about themselves in the planner prompt: {thin}"


@pytest.mark.os_agnostic
def test_the_planner_prompt_states_what_the_gate_runs(tmp_path: Path) -> None:
    """A registry built over a custom gate command reaches the prompt a real planner dispatch sends.

    Through the real ``dispatch_planner`` over a real coordinator, with the executor as the only
    double: the seam is "a node was handed a prompt", so the executor is where the prompt has to
    be read. It is the REGISTRY ARGUMENT that renders the ops text - ``planner.py`` builds the
    prompt from its own parameter and never from ``ctx.co.registry`` - which is why the
    coordinator here is wired plainly.
    """
    run_dir = fresh_run_dir(tmp_path)
    executor = PlanWritingExecutor(raw=None)  # writes no plan: this arm is about what the node was TOLD
    registry = build_op_registry(gate_command=("pytest", "-q"))
    co = wire(run_dir, executor, FakeScanner())

    asyncio.run(
        dispatch_planner(
            spec=planner_spec(),
            goal="anything",
            evidence={},
            ctx=PlanContext(co=co, cwd=run_dir.worktree("a")),
            registry=registry,
            limits=LIMITS,
            graph={},
            is_root=True,
            allocate_id=ids(),
        )
    )

    assert len(executor.requests) == 1, executor.requests
    assert "pytest -q" in executor.requests[0].prompt


@pytest.mark.os_agnostic
def test_the_wired_registry_describes_the_wired_gate(tmp_path: Path) -> None:
    """The production wiring's two halves cannot disagree: one command builds both.

    ``build_op_registry`` defaults its command so the twenty-odd test call sites need not name
    one; this is the arm that stops that default reaching production behind a gate wired with
    something else.
    """
    keyfile = tmp_path / "token"
    keyfile.write_text("not-a-real-token\n", encoding="utf-8")

    wiring = wire_kernel(
        policy_path=policy_path(),
        credential=OAuthTokenFile(keyfile),
        parallel=1,
        max_turns=5,
        deny_bash=(),
        deny_tools=(),
        notifier=NoNotifier(),
        gate_command=("pytest", "-q"),
    )

    assert wiring.gate_port.command == ("pytest", "-q")
    assert "pytest -q" in wiring.registry.get("gate:make-test").description
