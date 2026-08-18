"""CLI stories for ``agentdag run``: start, status, records, approve, resume (Task 17).

The injection follows ``tests/test_cli_graph_a.py::services_wiring`` exactly: build
``AppServices`` from ``build_production()`` with ``wire_kernel`` replaced by a closure
returning a fixed :class:`~agentdag.application.kernel.ports.KernelWiring` whose executor
is :class:`~tests.kernel_fakes.CommittingExecutor` and whose scope is
:class:`~agentdag.adapters.kernel.scope_none.NoScope` - everything else (the journal, the
run directory, the lock, the clock, the gate, git, the scanner, the policy) is the real
shipped adapter, exactly as ``tests/kernel_fakes.py`` already builds it for the
``run_coordinator``-level tests this module's fixtures were lifted from.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.scope_none import NoScope
from agentdag.application.kernel.ports import KernelWiring
from agentdag.composition import AppServices, build_production
from tests.kernel_fakes import CommittingExecutor, fleet, git, policy_path

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from click.testing import CliRunner


def services_with(executor: CommittingExecutor, tmp_path: Path) -> Callable[[], AppServices]:
    """Return a services factory whose ``wire_kernel`` hands back a fixed wiring over real adapters."""
    wiring = KernelWiring(
        journal_factory=JsonlJournal,
        lock=FileRunLock(),
        clock=UtcClock(),
        executors={"claude": executor},
        gate_port=MakeTestGate(lock=tmp_path / "gate.lock", command=(sys.executable, "-c", "raise SystemExit(0)")),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=load_policy(policy_path()),
        scope=NoScope(),
        runs_dir=tmp_path / "runs",
        parallel=2,
    )

    def _wire_kernel(**_: object) -> KernelWiring:
        return wiring

    prod = build_production()
    services = AppServices(
        get_config=prod.get_config,
        get_default_config_path=prod.get_default_config_path,
        deploy_configuration=prod.deploy_configuration,
        display_config=prod.display_config,
        send_email=prod.send_email,
        send_notification=prod.send_notification,
        load_email_config_from_dict=prod.load_email_config_from_dict,
        init_logging=prod.init_logging,
        wire_graph_a=prod.wire_graph_a,
        wire_kernel=_wire_kernel,
    )
    return lambda: services


def start_args(tmp_path: Path) -> list[str]:
    """Build ``run start graph-a`` argv over a fresh two-member fleet in ``tmp_path``."""
    args, _ = fleet(tmp_path, ["a", "b"], parallel=2)
    return [
        "run",
        "start",
        "graph-a",
        "--arg",
        f"repos_file={args.repos_file}",
        "--arg",
        f"brief_file={args.brief_file}",
        "--arg",
        f"scratch={args.scratch}",
        "--runs",
        str(tmp_path / "runs"),
        "--foreground",
    ]


@pytest.mark.os_agnostic
def test_run_start_foreground_suspends_then_approve_relaunches_and_pushes(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """``run start --foreground`` suspends at the push list; ``run approve`` relaunches and pushes."""
    (tmp_path / "runs").mkdir()
    ex = CommittingExecutor()
    obj = services_with(ex, tmp_path)

    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    assert started.exit_code == 0, started.output
    m = re.search(r"run (\S+) suspended at a_push_list", started.output)
    assert m, started.output
    run_id = m.group(1)

    status = cli_runner.invoke(cli_mod.cli, ["run", "status", run_id, "--runs", str(tmp_path / "runs")], obj=obj)
    assert status.exit_code == 0
    assert "suspended" in status.output and "a_push_list" in status.output

    records = cli_runner.invoke(cli_mod.cli, ["run", "records", run_id, "--runs", str(tmp_path / "runs")], obj=obj)
    expected_nodes = (
        "g_discover",
        "w_migrate@0",
        "w_migrate@1",
        "g_test@0",
        "g_test@1",
        "g_scan@0",
        "g_scan@1",
        "r_tally",
        "s_push_intent",
    )
    for node in expected_nodes:
        assert re.search(rf"{re.escape(node)}\s+0\s+done", records.output), records.output

    origin = tmp_path / "scratch" / "origin" / "a.git"
    before = git("rev-parse", "main", cwd=origin)
    runs_arg = str(tmp_path / "runs")
    approve_args = [
        "run",
        "approve",
        run_id,
        "a_push_list",
        "--decision",
        "approve",
        "--runs",
        runs_arg,
        "--foreground",
    ]
    approved = cli_runner.invoke(cli_mod.cli, approve_args, obj=obj)
    assert approved.exit_code == 0 and f"run {run_id} done" in approved.output, approved.output
    assert git("rev-parse", "main", cwd=origin) != before
    assert git("rev-parse", "main", cwd=tmp_path / "a") != git("rev-parse", "main", cwd=origin)
    assert sorted(ex.calls) == ["w_migrate@0", "w_migrate@1"]  # the relaunch replayed no new work

    no_relaunch_args = [
        "run",
        "approve",
        run_id,
        "a_push_list",
        "--decision",
        "approve",
        "--runs",
        runs_arg,
        "--no-relaunch",
    ]
    again = cli_runner.invoke(cli_mod.cli, no_relaunch_args, obj=obj)
    assert again.exit_code == ExitCode.INVALID_ARGUMENT and "already decided" in again.output


@pytest.mark.os_agnostic
def test_run_start_refuses_a_missing_runs_dir_and_a_bad_arg(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A missing ``--runs`` dir, and a bad ``--arg``, both refuse before any run dir is created."""
    obj = services_with(CommittingExecutor(), tmp_path)
    # Built ONCE: fleet() creates <scratch>/origin without exist_ok, so a second
    # start_args(tmp_path) call for the SAME tmp_path would collide with the first.
    base_args = start_args(tmp_path)
    missing_args = list(base_args)
    missing_args[missing_args.index("--runs") + 1] = str(tmp_path / "nope")
    missing = cli_runner.invoke(cli_mod.cli, missing_args, obj=obj)
    assert missing.exit_code == ExitCode.INVALID_ARGUMENT
    assert str(tmp_path / "nope") in missing.output

    (tmp_path / "runs").mkdir()
    bad = cli_runner.invoke(cli_mod.cli, [*base_args, "--arg", "parallel=zero"], obj=obj)
    assert bad.exit_code == ExitCode.INVALID_ARGUMENT
    assert "parallel" in bad.output
    assert not list((tmp_path / "runs").iterdir())  # a refused start creates no run dir


@pytest.mark.os_agnostic
def test_run_resume_refuses_a_done_run(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``run resume`` refuses a run whose state is already ``done``."""
    (tmp_path / "runs").mkdir()
    obj = services_with(CommittingExecutor(), tmp_path)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
    hold_args = [
        "run",
        "approve",
        run_id,
        "a_push_list",
        "--decision",
        "hold",
        "--runs",
        str(tmp_path / "runs"),
        "--foreground",
    ]
    cli_runner.invoke(cli_mod.cli, hold_args, obj=obj)

    resume_args = ["run", "resume", run_id, "--runs", str(tmp_path / "runs"), "--foreground"]
    resumed = cli_runner.invoke(cli_mod.cli, resume_args, obj=obj)
    assert resumed.exit_code == ExitCode.INVALID_ARGUMENT
    assert "done" in resumed.output
