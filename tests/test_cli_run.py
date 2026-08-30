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

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from kernel_fakes import CommittingExecutor, RecordingNotifier, fleet, git, policy_path

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.adapters.kernel.scope_none import NoScope
from agentdag.application.kernel.approve import DEADLINE_REASON, SYSTEM_IDENTITY, TIMER_TOKEN_ID
from agentdag.application.kernel.cancel import scope_unit
from agentdag.application.kernel.ports import KernelWiring, LaunchResult, ScopeHandle
from agentdag.composition import AppServices, build_production
from agentdag.composition.kernel import build_op_registry
from agentdag.domain.journal import ResultLine
from agentdag.domain.keys import hash8
from agentdag.domain.models import ErrorType, NodeError, NodeStatus, ResultRecord, RetryGrant

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from click.testing import CliRunner, Result

    from agentdag.application.kernel.notify import Notifier
    from agentdag.application.kernel.ports import Clock, Scope


@dataclass(frozen=True, slots=True)
class RecordedLaunch:
    """One :meth:`RecordingScope.start` call, exactly as the CLI made it."""

    unit: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path


class RecordingScope:
    """A fake :class:`~agentdag.application.kernel.ports.Scope` that never launches a real process.

    Records every :meth:`start` call (so a test can assert what argv the CLI built,
    e.g. whether ``--parallel``/``--policy`` were forwarded) and answers
    :meth:`confirm` with a canned :class:`~agentdag.application.kernel.ports.LaunchResult`
    - never a real subprocess, so this is safe to use for a BACKGROUND (non-``--foreground``)
    ``run start`` without ever spending a real Claude call.

    A plain class with a typed ``__init__``, not ``@dataclass``: a bare
    ``field(default_factory=list)`` infers ``list[Unknown]`` under pyright strict (the
    annotation is ignored), which this sidesteps.

    ``is_alive_result``/``kill_result`` are DELIBERATELY separate from ``confirm_alive``
    (M3): a launch-confirm test and a cancel/sweep test ask two different questions of
    this fake, and conflating them would make one kind of test's fixture accidentally
    control the other's outcome.
    """

    def __init__(
        self,
        *,
        confirm_alive: bool = True,
        confirm_stderr: str = "",
        cross_process_capable: bool = True,
        is_alive_result: bool = True,
        kill_result: bool = True,
    ) -> None:
        """Bind the canned answers; ``calls``/``kill_calls``/``is_alive_calls`` start empty."""
        self.confirm_alive = confirm_alive
        self.confirm_stderr = confirm_stderr
        self.cross_process_capable = cross_process_capable
        self.is_alive_result = is_alive_result
        self.kill_result = kill_result
        self.calls: list[RecordedLaunch] = []
        self.is_alive_calls: list[ScopeHandle] = []
        self.kill_calls: list[ScopeHandle] = []

    def start(self, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> ScopeHandle:
        """Record the call and return a handle naming it; no process is ever started."""
        self.calls.append(RecordedLaunch(unit=unit, argv=tuple(argv), env=dict(env), cwd=cwd))
        return ScopeHandle(unit=unit, pid=-1, log_path=cwd / "launch.log")

    def confirm(self, handle: ScopeHandle, *, timeout_s: float) -> LaunchResult:
        """Return the canned result this instance was built with."""
        del handle, timeout_s
        return LaunchResult(alive=self.confirm_alive, stderr=self.confirm_stderr)

    def is_alive(self, handle: ScopeHandle) -> bool:
        """Record the call and return the canned ``is_alive_result``."""
        self.is_alive_calls.append(handle)
        return self.is_alive_result

    def kill(self, handle: ScopeHandle) -> bool:
        """Record the call and return the canned ``kill_result`` - never trusted blindly by the caller."""
        self.kill_calls.append(handle)
        return self.kill_result


def services_with(
    executor: CommittingExecutor,
    tmp_path: Path,
    *,
    scope: Scope | None = None,
    wire_calls: list[Mapping[str, object]] | None = None,
    clock: Clock | None = None,
    notifier: Notifier | None = None,
) -> Callable[[], AppServices]:
    """Return a services factory whose ``wire_kernel`` hands back a fixed wiring over real adapters.

    ``scope`` defaults to a real :class:`NoScope`; pass a :class:`RecordingScope` for a
    test that must exercise the BACKGROUND launch path without spawning a real process.
    ``wire_calls``, if given, is appended one dict of kwargs per ``wire_kernel`` call -
    for a test asserting what ``_build_wiring`` (fix round 1) resolved ``--parallel``/
    ``--policy`` to, across BOTH times ``run start --foreground`` calls it.
    ``clock`` defaults to a real :class:`UtcClock`; pass a :class:`MovableClock` for a
    test about ``decide_by``, which is a day out from the run's own start and can only
    be reached by moving the clock the whole run reads (Task 22).
    ``notifier`` defaults to the shipped no-op sink, which is also what an operator who
    configured none gets; pass a :class:`~kernel_fakes.RecordingNotifier` for a test
    about what the run TOLD somebody (Task 23).
    """
    wiring = KernelWiring(
        journal_factory=JsonlJournal,
        lock=FileRunLock(),
        clock=clock if clock is not None else UtcClock(),
        executors={"claude": executor},
        gate_port=MakeTestGate(command=(sys.executable, "-c", "raise SystemExit(0)")),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=load_policy(policy_path()),
        registry=build_op_registry(),
        scope=scope if scope is not None else NoScope(),
        sandbox=NoSandbox(),
        notifier=notifier if notifier is not None else NoNotifier(),
        parallel=2,
    )

    def _wire_kernel(**kwargs: object) -> KernelWiring:
        if wire_calls is not None:
            wire_calls.append(kwargs)
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


def start_args(tmp_path: Path, *, foreground: bool = True, extra: Sequence[str] = ()) -> list[str]:
    """Build ``run start graph-a`` argv over a fresh two-member fleet in ``tmp_path``.

    ``foreground=False`` omits ``--foreground`` (a background launch); ``extra`` appends
    further flags (``--parallel``, ``--policy``) after ``--runs``.
    """
    args, _ = fleet(tmp_path, ["a", "b"])
    argv = [
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
        *extra,
    ]
    if foreground:
        argv.append("--foreground")
    return argv


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
    # The run is DONE (the --foreground approve above already relaunched it to completion),
    # so this is no longer "already decided" - it is not waiting on ANY decision any more.
    # _decision_for (fix round 1) checks status BEFORE it would ever reach that message.
    assert again.exit_code == ExitCode.INVALID_ARGUMENT
    assert "not waiting on a decision" in again.output and "done" in again.output


@pytest.mark.os_agnostic
def test_run_start_refuses_a_missing_runs_dir_and_a_missing_required_arg(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A missing ``--runs`` dir, and a missing required ``--arg``, both refuse before any run dir is created."""
    obj = services_with(CommittingExecutor(), tmp_path)
    # fleet() built ONCE: it creates <scratch>/origin without exist_ok, so a second call
    # for the SAME tmp_path would collide with the first.
    args, _ = fleet(tmp_path, ["a", "b"])
    missing_dir_args = [
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
        str(tmp_path / "nope"),
        "--foreground",
    ]
    missing = cli_runner.invoke(cli_mod.cli, missing_dir_args, obj=obj)
    assert missing.exit_code == ExitCode.INVALID_ARGUMENT
    assert str(tmp_path / "nope") in missing.output

    (tmp_path / "runs").mkdir()
    # No `scratch=` --arg at all: GraphAArgs.scratch is required with no default, so
    # pydantic's own ValidationError message names it (GraphAArgs carries no `parallel`
    # field any more - see fix round 1 - so a merely bad VALUE would coerce fine into most
    # of its str/Path fields; a MISSING required field is what genuinely fails validation).
    missing_required_arg_args = [
        "run",
        "start",
        "graph-a",
        "--arg",
        f"repos_file={args.repos_file}",
        "--arg",
        f"brief_file={args.brief_file}",
        "--runs",
        str(tmp_path / "runs"),
        "--foreground",
    ]
    bad = cli_runner.invoke(cli_mod.cli, missing_required_arg_args, obj=obj)
    assert bad.exit_code == ExitCode.INVALID_ARGUMENT
    assert "scratch" in bad.output
    assert not list((tmp_path / "runs").iterdir())  # a refused start creates no run dir


@pytest.mark.os_agnostic
def test_a_failed_run_prints_a_scrubbed_exception_not_the_secret_it_carried(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The CLI's exception-to-output sink redacts, like every other sink that text reaches.

    An exception's own text is not safe by construction: it quotes whatever it was handed,
    and a lock file's recorded holder is a string read off disk. The dispatcher and the
    executor already scrub theirs before writing a record; the console is a sink too.

    Driven end to end through the real lock adapter: the run dir is given a lock file
    naming a LIVE holder (this very process, so the liveness test really passes) whose
    host field carries a token-shaped string, and ``run resume`` then fails with the
    ``LockHeld`` that quotes it.
    """
    (tmp_path / "runs").mkdir()
    obj = services_with(CommittingExecutor(), tmp_path)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
    secret = "sk-ant-oat01-DEADBEEF"
    holder = current_holder().model_copy(update={"host": secret})
    (tmp_path / "runs" / run_id / "lock").write_text(holder.model_dump_json(), encoding="utf-8")

    failed = cli_runner.invoke(
        cli_mod.cli, ["run", "resume", run_id, "--runs", str(tmp_path / "runs"), "--foreground"], obj=obj
    )

    assert failed.exit_code == ExitCode.GENERAL_ERROR
    assert f"run {run_id} failed:" in failed.output
    assert secret not in failed.output
    assert "[scrubbed]" in failed.output


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


@pytest.mark.os_agnostic
def test_run_cancel_verifies_and_a_second_call_is_idempotent(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A verified cancel: ``cancelling`` at once, then ``verified: true`` in the same
    invocation, journaled and reflected in ``state.json``; a repeat call short-circuits
    without asking the scope to kill anything a second time."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(cross_process_capable=True, is_alive_result=True, kill_result=True)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
    runs_arg = str(tmp_path / "runs")
    # RecordingScope's is_alive_result is a CANNED value, not a real "was start() ever
    # called" check (unlike NoScope's own in-memory tracking) - the initial `run start
    # --foreground`'s OWN startup sweep therefore also sees "alive" and kills once, for a
    # run whose unit was never really started. Only the CANCEL call below is under test.
    scope.kill_calls.clear()

    cancelled = cli_runner.invoke(cli_mod.cli, ["run", "cancel", run_id, "--runs", runs_arg], obj=obj)

    assert cancelled.exit_code == 0, cancelled.output
    assert f"run {run_id} cancelling" in cancelled.output
    assert f"run {run_id} cancel verified: true" in cancelled.output
    assert len(scope.kill_calls) == 1

    status = cli_runner.invoke(cli_mod.cli, ["run", "status", run_id, "--runs", runs_arg], obj=obj)
    assert "cancelled" in status.output

    again = cli_runner.invoke(cli_mod.cli, ["run", "cancel", run_id, "--runs", runs_arg], obj=obj)
    assert again.exit_code == 0, again.output
    assert f"run {run_id} cancelled (verified: true)" in again.output
    assert len(scope.kill_calls) == 1  # not asked to kill an already-verified scope again


@pytest.mark.os_agnostic
def test_run_cancel_under_noscope_reports_unverified_with_a_named_reason(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The STOP condition: a scope that cannot confirm a cross-process kill (a real
    :class:`~agentdag.adapters.kernel.scope_none.NoScope`, not a fake) never claims a
    verified cancel it never actually confirmed."""
    (tmp_path / "runs").mkdir()
    obj = services_with(CommittingExecutor(), tmp_path, scope=NoScope())
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)

    cancelled = cli_runner.invoke(cli_mod.cli, ["run", "cancel", run_id, "--runs", str(tmp_path / "runs")], obj=obj)

    assert cancelled.exit_code == 0, cancelled.output
    assert f"run {run_id} cancel verified: false" in cancelled.output
    assert "NoScope" in cancelled.output


@pytest.mark.os_agnostic
def test_run_cancel_refuses_a_done_run(cli_runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    ex = CommittingExecutor()
    obj = services_with(ex, tmp_path)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
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

    cancelled = cli_runner.invoke(cli_mod.cli, ["run", "cancel", run_id, "--runs", runs_arg], obj=obj)

    assert cancelled.exit_code == ExitCode.INVALID_ARGUMENT
    assert "done" in cancelled.output
    assert not (tmp_path / "runs" / run_id / "decisions" / "_run.cancel.json").is_file()


@pytest.mark.os_agnostic
@pytest.mark.parametrize("verb", ["cancelling", "cancelled"])
def test_run_resume_refuses_a_cancelling_or_a_cancelled_run(cli_runner: CliRunner, tmp_path: Path, verb: str) -> None:
    (tmp_path / "runs").mkdir()
    kill_result = verb == "cancelled"
    # kill_result starts True so the INITIAL start below's own startup sweep (M3) confirms
    # trivially - RecordingScope's is_alive_result is a blanket canned True regardless of
    # whether anything was ever really started, so a False kill_result here would make
    # that first `run start --foreground` itself fail before the run exists at all. Only
    # the CANCEL call under test should see the canned verdict this parametrization is about.
    scope = RecordingScope(cross_process_capable=True, is_alive_result=True, kill_result=True)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
    runs_arg = str(tmp_path / "runs")
    scope.kill_result = kill_result
    cli_runner.invoke(cli_mod.cli, ["run", "cancel", run_id, "--runs", runs_arg], obj=obj)

    resumed = cli_runner.invoke(cli_mod.cli, ["run", "resume", run_id, "--runs", runs_arg, "--foreground"], obj=obj)

    assert resumed.exit_code == ExitCode.INVALID_ARGUMENT
    assert verb in resumed.output


@pytest.mark.os_agnostic
def test_run_resume_stops_a_scope_a_dead_coordinator_left_behind_before_relaunching(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The startup sweep (Step 4): a resume calls ``scope.kill`` for THIS run's own unit
    name before the coordinator dispatches anything, whether or not one is truly stuck -
    the fake reports ``is_alive`` unconditionally, standing in for a scope a dead
    coordinator left draining."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(cross_process_capable=True, is_alive_result=True, kill_result=True)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
    runs_arg = str(tmp_path / "runs")
    scope.kill_calls.clear()  # only the RESUME below is under test, not the initial start

    # No decision is recorded: the coordinator replays back to the SAME suspend, which is
    # exactly what proves the sweep fired BEFORE it - a resume that never dispatches
    # anything new still had to check for a left-behind scope first.
    resumed = cli_runner.invoke(cli_mod.cli, ["run", "resume", run_id, "--runs", runs_arg, "--foreground"], obj=obj)

    assert resumed.exit_code == 0, resumed.output
    assert f"run {run_id} suspended" in resumed.output
    assert len(scope.kill_calls) == 1
    assert scope.kill_calls[0].unit == scope_unit(run_id)


@pytest.mark.os_agnostic
def test_run_resume_foreground_fails_clearly_when_the_startup_sweep_cannot_confirm_stopped(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The still-draining case (the M2 crash probe's own ~40s window): a foreground
    resume must not proceed into the coordinator while a scope a dead coordinator left
    behind could not be confirmed stopped - it fails clearly instead."""
    (tmp_path / "runs").mkdir()
    # kill_result starts True so the INITIAL start's own startup sweep confirms trivially
    # (see the comment on test_run_resume_refuses_a_cancelling_or_a_cancelled_run for why);
    # only the RESUME below is meant to see the unconfirmed sweep under test.
    scope = RecordingScope(cross_process_capable=True, is_alive_result=True, kill_result=True)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
    runs_arg = str(tmp_path / "runs")
    scope.kill_result = False

    resumed = cli_runner.invoke(cli_mod.cli, ["run", "resume", run_id, "--runs", runs_arg, "--foreground"], obj=obj)

    assert resumed.exit_code == ExitCode.GENERAL_ERROR, resumed.output
    assert f"run {run_id} failed" in resumed.output


@pytest.mark.os_agnostic
def test_run_resume_background_fails_clearly_when_the_startup_sweep_cannot_confirm_stopped(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Same case as the foreground test above, through :func:`_relaunch`'s own BACKGROUND
    path: it must never reach ``scope.start()`` under the SAME unit name a still-draining
    scope occupies (``systemd-run`` would otherwise refuse the collision outright)."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(cross_process_capable=True, is_alive_result=True, kill_result=True)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
    runs_arg = str(tmp_path / "runs")
    scope.kill_result = False
    scope.calls.clear()  # only the resume below is under test

    resumed = cli_runner.invoke(cli_mod.cli, ["run", "resume", run_id, "--runs", runs_arg], obj=obj)

    assert resumed.exit_code == ExitCode.GENERAL_ERROR, resumed.output
    assert f"run {run_id} failed" in resumed.output
    assert not scope.calls  # never reached scope.start()


@pytest.mark.os_agnostic
def test_run_start_background_confirms_the_launch_and_omits_unset_flags(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A BACKGROUND launch is confirmed before ``started`` prints, and forwards no unset flag."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(confirm_alive=True)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)

    result = cli_runner.invoke(cli_mod.cli, start_args(tmp_path, foreground=False), obj=obj)

    assert result.exit_code == 0, result.output
    assert re.search(r"run \S+ started \(unit .+, log .+\)", result.output), result.output
    assert len(scope.calls) == 1
    argv = scope.calls[0].argv
    assert "--parallel" not in argv
    assert "--policy" not in argv


@pytest.mark.os_agnostic
def test_run_start_background_forwards_parallel_and_policy_when_given(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``--parallel``/``--policy`` on ``run start`` reach the ``_coordinate`` argv it launches."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(confirm_alive=True)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)
    alt_policy = policy_path()  # a real, existing file - this proves FORWARDING, not content

    argv = start_args(tmp_path, foreground=False, extra=["--parallel", "3", "--policy", str(alt_policy)])
    result = cli_runner.invoke(cli_mod.cli, argv, obj=obj)

    assert result.exit_code == 0, result.output
    assert len(scope.calls) == 1
    launched = scope.calls[0].argv
    assert launched[launched.index("--parallel") + 1] == "3"
    assert launched[launched.index("--policy") + 1] == str(alt_policy)


@pytest.mark.os_agnostic
def test_run_start_background_reports_a_failed_launch_and_exits_non_zero(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A launch ``scope.confirm`` reports as failed exits non-zero and echoes its captured stderr."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(confirm_alive=False, confirm_stderr="Failed to start transient scope unit: boom")
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)

    result = cli_runner.invoke(cli_mod.cli, start_args(tmp_path, foreground=False), obj=obj)

    assert result.exit_code == ExitCode.GENERAL_ERROR, result.output
    assert "failed to start" in result.output
    assert "Failed to start transient scope unit: boom" in result.output
    assert len(scope.calls) == 1  # start() WAS called; confirm() is what caught the failure


@pytest.mark.os_agnostic
def test_run_start_foreground_uses_its_own_parallel_and_policy_for_both_wiring_builds(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """``run start --foreground --parallel N --policy FILE`` resolves the SAME override
    both times it builds a :class:`~agentdag.application.kernel.ports.KernelWiring` -
    once for the ``state.json`` pre-write, once inside ``_run_foreground`` for the
    actual coordinator run - rather than the second build silently re-deriving
    config-only defaults that could disagree with what was actually given (fix round 1).
    """
    (tmp_path / "runs").mkdir()
    calls: list[Mapping[str, object]] = []
    obj = services_with(CommittingExecutor(), tmp_path, wire_calls=calls)
    alt_policy = policy_path()  # a real, existing file - only the VALUE THREADED is under test

    argv = start_args(tmp_path, extra=["--parallel", "3", "--policy", str(alt_policy)])
    result = cli_runner.invoke(cli_mod.cli, argv, obj=obj)

    assert result.exit_code == 0, result.output
    assert len(calls) == 2, calls  # the state pre-write's own build, then _run_foreground's
    assert all(call["parallel"] == 3 for call in calls), calls
    assert all(call["policy_path"] == alt_policy for call in calls), calls


# ---------------------------------------------------------------------------------
# Task 22: the approve deadline's owner, end to end. `agentdag run apply-deadlines`
# applies the payload's default once its decide_by has passed and relaunches the run;
# a human answering the same payload at the same moment is refused, not overwritten.
# ---------------------------------------------------------------------------------


class MovableClock:
    """A :class:`~agentdag.application.kernel.ports.Clock` a test moves between invocations.

    The whole wiring reads one clock, so pinning it also pins the ``run_started`` line
    ``graph-a``'s ``decide_by`` is derived from - which is how a test reaches a deadline a
    day out without waiting for it, and how it proves that deadline does not MOVE when the
    clock does.
    """

    def __init__(self, now: datetime) -> None:
        """Bind the first instant; assign :attr:`at` to move it."""
        self.at = now

    def now(self) -> datetime:
        """Return the instant currently set."""
        return self.at


_T0 = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)
"""The instant a deadline test's run starts at; graph-a derives its decide_by from it."""


def started_run(cli_runner: CliRunner, tmp_path: Path, obj: Callable[[], AppServices]) -> str:
    """Start a run that suspends at the push list, and return its id."""
    (tmp_path / "runs").mkdir()
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    assert started.exit_code == 0, started.output
    match = re.search(r"run (\S+) suspended at a_push_list", started.output)
    assert match, started.output
    return match.group(1)


@pytest.mark.os_agnostic
def test_apply_deadlines_records_and_announces_a_run_whose_coordinator_died(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The crash path end to end, through the CLI that the systemd timer actually runs.

    The kernel-level behaviour is pinned in ``test_kernel_notify.py``; what THIS proves is
    the wiring - that the periodic pass reaches ``record_crash`` at all, and hands it the
    sink ``kernel.notify`` resolved rather than a fresh no-op nobody hears.
    """
    notifier = RecordingNotifier()
    obj = services_with(CommittingExecutor(crash_on="w_migrate@1"), tmp_path, notifier=notifier)
    (tmp_path / "runs").mkdir()
    runs_arg = str(tmp_path / "runs")

    crashed = cli_runner.invoke(cli_mod.cli, start_args(tmp_path, extra=["--parallel", "1"]), obj=obj)

    assert crashed.exit_code != 0, crashed.output  # the executor took the process down mid-run
    run_id = _only_run_id(tmp_path)
    assert _status_of(tmp_path, run_id) == "running"  # nothing was written on the way out
    assert notifier.events == []  # and nobody was told, which is the whole problem

    swept = cli_runner.invoke(cli_mod.cli, ["run", "apply-deadlines", "--runs", runs_arg], obj=obj)

    assert swept.exit_code == 0, swept.output
    assert "recorded 1 crashed run(s)" in swept.output
    assert _status_of(tmp_path, run_id) == "crashed"
    assert [event.status.value for event in notifier.events] == ["crashed"]

    again = cli_runner.invoke(cli_mod.cli, ["run", "apply-deadlines", "--runs", runs_arg], obj=obj)

    assert "recorded 0 crashed run(s)" in again.output
    assert len(notifier.events) == 1  # the recorded state is the dedup


def _only_run_id(tmp_path: Path) -> str:
    """Return the id of the one run under ``runs/``, refusing to guess when there are several."""
    ids = [entry.name for entry in (tmp_path / "runs").iterdir() if entry.is_dir()]
    assert len(ids) == 1, ids
    return ids[0]


def _status_of(tmp_path: Path, run_id: str) -> str:
    """Read a run's status straight off its state file."""
    return str(json.loads((tmp_path / "runs" / run_id / "state.json").read_text(encoding="utf-8"))["status"])


def cursor_hash(tmp_path: Path, run_id: str) -> str:
    """Read the payload hash ``state.json`` says the run is suspended on."""
    state = json.loads((tmp_path / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "suspended"
    return str(state["cursor_payload_hash"])


def decide_by_of(tmp_path: Path, run_id: str) -> datetime:
    """Read the deadline off the suspended payload itself.

    Read, never recomputed from the workflow's own interval constant: that constant is
    exactly what these tests must not hold a second copy of, and reading the field is also
    what the deadline owner does.
    """
    payload_path = next((tmp_path / "runs" / run_id / "nodes" / "a_push_list").glob("*/payload.json"))
    return datetime.fromisoformat(json.loads(payload_path.read_text(encoding="utf-8"))["decide_by"])


@pytest.mark.os_agnostic
def test_apply_deadlines_applies_the_default_relaunches_and_binds_it_to_the_unchanged_payload(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The deadline owner's whole job, and the identity property that makes it safe to repeat.

    The decision is bound to the payload hash the suspend recorded, and the run reaches
    ``done`` in ONE relaunch. Both would fail if ``decide_by`` were derived from the clock
    rather than from the run's own ``run_started`` line: this pass runs with the clock a
    day and a second ahead of the start, so a clock-derived deadline would give the
    relaunch a payload with a DIFFERENT hash - a new question, which re-dispatches the
    approve node and suspends again instead of applying the answer just recorded.
    """
    clock = MovableClock(_T0)
    obj = services_with(CommittingExecutor(), tmp_path, clock=clock)
    run_id = started_run(cli_runner, tmp_path, obj)
    runs_arg = str(tmp_path / "runs")
    suspended_hash = cursor_hash(tmp_path, run_id)
    origin = tmp_path / "scratch" / "origin" / "a.git"
    before = git("rev-parse", "main", cwd=origin)
    clock.at = decide_by_of(tmp_path, run_id) + timedelta(seconds=1)

    applied = cli_runner.invoke(cli_mod.cli, ["run", "apply-deadlines", "--runs", runs_arg, "--foreground"], obj=obj)

    assert applied.exit_code == 0, applied.output
    assert f"run {run_id}: applied default 'hold' at a_push_list" in applied.output
    assert "applied 1 default decision(s)" in applied.output
    assert f"run {run_id} done" in applied.output  # the relaunch this pass made, to completion
    decisions = sorted((tmp_path / "runs" / run_id / "decisions").glob("*.json"))
    assert [path.name for path in decisions] == [f"a_push_list.{hash8(suspended_hash)}.json"]
    recorded = json.loads(decisions[0].read_text(encoding="utf-8"))
    assert recorded["payload_hash"] == suspended_hash  # the payload the human was shown, unmoved
    assert (recorded["decision"], recorded["by"], recorded["reason"]) == ("hold", SYSTEM_IDENTITY, DEADLINE_REASON)
    assert recorded["token_id"] == TIMER_TOKEN_ID
    folded = [
        json.loads(line)
        for line in (tmp_path / "runs" / run_id / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event"] == "approve_decision"
    ]
    assert len(folded) == 1
    assert folded[0]["by"] == SYSTEM_IDENTITY
    assert git("rev-parse", "main", cwd=origin) == before  # 'hold' has no external effect, and none happened


@pytest.mark.os_agnostic
def test_apply_deadlines_leaves_a_run_alone_before_its_decide_by(cli_runner: CliRunner, tmp_path: Path) -> None:
    """One second short of the deadline: nothing written, nothing relaunched, and it says why."""
    clock = MovableClock(_T0)
    obj = services_with(CommittingExecutor(), tmp_path, clock=clock)
    run_id = started_run(cli_runner, tmp_path, obj)
    runs_arg = str(tmp_path / "runs")
    clock.at = decide_by_of(tmp_path, run_id) - timedelta(seconds=1)

    applied = cli_runner.invoke(cli_mod.cli, ["run", "apply-deadlines", "--runs", runs_arg], obj=obj)

    assert applied.exit_code == 0, applied.output
    assert "not due until" in applied.output
    assert "applied 0 default decision(s)" in applied.output
    assert not list((tmp_path / "runs" / run_id / "decisions").glob("*.json"))
    status = cli_runner.invoke(cli_mod.cli, ["run", "status", run_id, "--runs", runs_arg], obj=obj)
    assert "suspended" in status.output


@pytest.mark.os_agnostic
def test_a_human_approve_after_the_deadline_default_is_refused_and_pushes_nothing(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The race that matters, at the CLI: exactly one decision wins and the loser is REFUSED.

    The human's option here is the one with the external effect, so this also proves the
    refusal is not cosmetic: a decision that was never recorded must not push.
    """
    clock = MovableClock(_T0)
    obj = services_with(CommittingExecutor(), tmp_path, clock=clock)
    run_id = started_run(cli_runner, tmp_path, obj)
    runs_arg = str(tmp_path / "runs")
    origin = tmp_path / "scratch" / "origin" / "a.git"
    before = git("rev-parse", "main", cwd=origin)
    clock.at = decide_by_of(tmp_path, run_id) + timedelta(seconds=1)
    applied = cli_runner.invoke(cli_mod.cli, ["run", "apply-deadlines", "--runs", runs_arg, "--no-relaunch"], obj=obj)
    assert applied.exit_code == 0, applied.output
    assert "applied 1 default decision(s)" in applied.output

    late = cli_runner.invoke(
        cli_mod.cli,
        ["run", "approve", run_id, "a_push_list", "--decision", "approve", "--runs", runs_arg, "--no-relaunch"],
        obj=obj,
    )

    assert late.exit_code == ExitCode.INVALID_ARGUMENT, late.output
    assert "already decided for this payload" in late.output
    decisions = sorted((tmp_path / "runs" / run_id / "decisions").glob("*.json"))
    assert len(decisions) == 1
    assert json.loads(decisions[0].read_text(encoding="utf-8"))["by"] == SYSTEM_IDENTITY
    assert git("rev-parse", "main", cwd=origin) == before


@pytest.mark.os_agnostic
def test_run_approve_refuses_a_wrong_node_an_unoffered_option_and_a_repeat_answer(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Three of ``_decision_for``'s refusals, each naming what the caller got wrong."""
    obj = services_with(CommittingExecutor(), tmp_path)
    run_id = started_run(cli_runner, tmp_path, obj)
    runs_arg = str(tmp_path / "runs")

    def approve(node_id: str, decision: str) -> Result:
        argv = ["run", "approve", run_id, node_id, "--decision", decision, "--runs", runs_arg, "--no-relaunch"]
        return cli_runner.invoke(cli_mod.cli, argv, obj=obj)

    wrong_node = approve("g_discover", "hold")
    assert wrong_node.exit_code == ExitCode.INVALID_ARGUMENT
    assert "suspended on 'a_push_list', not 'g_discover'" in wrong_node.output

    unoffered = approve("a_push_list", "maybe")
    assert unoffered.exit_code == ExitCode.INVALID_ARGUMENT
    assert "is not one of" in unoffered.output and "['approve', 'hold']" in unoffered.output

    assert approve("a_push_list", "hold").exit_code == 0
    repeat = approve("a_push_list", "approve")
    assert repeat.exit_code == ExitCode.INVALID_ARGUMENT
    assert "already decided for this payload" in repeat.output
    assert len(list((tmp_path / "runs" / run_id / "decisions").glob("*.json"))) == 1


@pytest.mark.os_agnostic
def test_run_approve_refuses_a_payload_that_does_not_match_state_json(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``_decision_for``'s three payload refusals, in order of how broken the run dir is.

    A decision is bound to the payload's content hash, so the CLI answers only a payload
    it can prove is the one ``state.json`` names - never whatever bytes happen to sit at
    the path.
    """
    obj = services_with(CommittingExecutor(), tmp_path)
    run_id = started_run(cli_runner, tmp_path, obj)
    runs_arg = str(tmp_path / "runs")
    root = tmp_path / "runs" / run_id
    payload_path = next((root / "nodes" / "a_push_list").glob("*/payload.json"))

    def approve() -> Result:
        argv = ["run", "approve", run_id, "a_push_list", "--decision", "hold", "--runs", runs_arg, "--no-relaunch"]
        return cli_runner.invoke(cli_mod.cli, argv, obj=obj)

    payload_path.write_text(payload_path.read_text(encoding="utf-8").replace("push list", "other list"), "utf-8")
    tampered = approve()
    assert tampered.exit_code == ExitCode.INVALID_ARGUMENT
    assert "does not match state.json's cursor_payload_hash" in tampered.output

    payload_path.unlink()
    missing = approve()
    assert missing.exit_code == ExitCode.INVALID_ARGUMENT
    assert "the payload file for 'a_push_list' is missing" in missing.output

    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    state["cursor_payload_hash"] = None
    (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    hashless = approve()
    assert hashless.exit_code == ExitCode.INVALID_ARGUMENT
    assert "names no payload hash" in hashless.output
    assert not list((root / "decisions").glob("*.json"))  # no refusal wrote a decision


@pytest.mark.os_agnostic
def test_the_shipped_timer_unit_runs_the_command_that_applies_the_default() -> None:
    """The operator installs these files by hand, so nothing else notices if the verb is renamed."""
    deploy = Path(__file__).resolve().parent.parent / "deploy"
    service = (deploy / "agentdag-approve-timer.service").read_text(encoding="utf-8")
    timer = (deploy / "agentdag-approve-timer.timer").read_text(encoding="utf-8")

    assert "run apply-deadlines" in service
    assert "Type=oneshot" in service
    assert "Unit=agentdag-approve-timer.service" in timer
    assert "WantedBy=timers.target" in timer
    assert "Persistent=true" in timer  # a machine off across a decide_by still applies the default


def _started_with_a_failed_work_node(cli_runner: CliRunner, tmp_path: Path) -> tuple[str, CommittingExecutor, object]:
    """Drive graph A once with ``w_migrate@0`` reporting failure, and return the run id.

    A work node's failure is the case the automatic rule deliberately never retries
    (design 2.3 rule 5 owns a model node's retry), so it is exactly what ``run retry``
    exists for.
    """
    (tmp_path / "runs").mkdir()
    ex = CommittingExecutor(fail_on="w_migrate@0")
    obj = services_with(ex, tmp_path)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    assert started.exit_code == 0, started.output
    m = re.search(r"run (\S+) ", started.output)
    assert m, started.output
    return m.group(1), ex, obj


@pytest.mark.os_agnostic
def test_run_retry_grants_a_failed_node_another_attempt_and_relaunches(cli_runner: CliRunner, tmp_path: Path) -> None:
    run_id, ex, obj = _started_with_a_failed_work_node(cli_runner, tmp_path)
    runs_arg = str(tmp_path / "runs")
    assert ex.calls.count("w_migrate@0") == 1

    retried = cli_runner.invoke(
        cli_mod.cli, ["run", "retry", run_id, "w_migrate@0", "--runs", runs_arg, "--foreground"], obj=obj
    )

    assert retried.exit_code == 0, retried.output
    assert "w_migrate@0" in retried.output
    assert ex.calls.count("w_migrate@0") == 2  # the granted attempt really ran


@pytest.mark.os_agnostic
def test_run_retry_refuses_a_node_the_run_has_no_record_for(cli_runner: CliRunner, tmp_path: Path) -> None:
    run_id, _ex, obj = _started_with_a_failed_work_node(cli_runner, tmp_path)

    result = cli_runner.invoke(
        cli_mod.cli, ["run", "retry", run_id, "w_nonesuch", "--runs", str(tmp_path / "runs")], obj=obj
    )

    assert result.exit_code == ExitCode.INVALID_ARGUMENT
    assert "no record" in result.output and "w_nonesuch" in result.output


@pytest.mark.os_agnostic
def test_run_retry_refuses_a_node_whose_latest_record_passed(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Latest, not latest-failed: a node that failed and later succeeded is not dragged back."""
    run_id, _ex, obj = _started_with_a_failed_work_node(cli_runner, tmp_path)

    result = cli_runner.invoke(
        cli_mod.cli, ["run", "retry", run_id, "g_discover", "--runs", str(tmp_path / "runs")], obj=obj
    )

    assert result.exit_code == ExitCode.INVALID_ARGUMENT
    assert "nothing to retry" in result.output and "done" in result.output


@pytest.mark.os_agnostic
def test_run_retry_refuses_a_second_grant_for_the_same_failure(cli_runner: CliRunner, tmp_path: Path) -> None:
    """One grant buys one attempt, so a doubled command must not mint a second."""
    run_id, _ex, obj = _started_with_a_failed_work_node(cli_runner, tmp_path)
    runs_arg = str(tmp_path / "runs")
    args = ["run", "retry", run_id, "w_migrate@0", "--runs", runs_arg, "--no-relaunch"]
    first = cli_runner.invoke(cli_mod.cli, args, obj=obj)
    assert first.exit_code == 0, first.output

    second = cli_runner.invoke(cli_mod.cli, args, obj=obj)

    assert second.exit_code == ExitCode.INVALID_ARGUMENT
    assert "already" in second.output


@pytest.mark.os_agnostic
def test_the_hidden_coordinate_entry_point_accepts_the_reason_a_background_retry_forwards(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """``_relaunch`` builds ``_coordinate``'s argv, so its ``--reason`` choice has to accept
    what a retry passes - a foreground-only test never goes near that argv."""
    run_id, _ex, obj = _started_with_a_failed_work_node(cli_runner, tmp_path)

    result = cli_runner.invoke(
        cli_mod.cli,
        ["run", "_coordinate", run_id, "--runs", str(tmp_path / "runs"), "--reason", "retry"],
        obj=obj,
    )

    assert result.exit_code == 0, result.output


def _append_failed_attempts(run_dir: Path, node_id: str, attempts: range) -> list[str]:
    """Append one failed result line per attempt for ``node_id``; return the keys, in order.

    This is the on-disk state a run left under a HIGHER ``max_attempts`` than the one the
    next launch will resolve - the policy is not recorded on the run, and neither ``run retry``
    nor the relaunch it fires carries ``--policy``.
    """
    journal = JsonlJournal(run_dir / "journal.jsonl", run_dir / "audit.jsonl")
    keys: list[str] = []
    for attempt in attempts:
        key = "v2:sha256:" + f"{attempt:02x}" * 32  # distinct in the LEADING hex, which names the file
        keys.append(key)
        record = ResultRecord(
            node_id=node_id,
            attempt=attempt,
            status=NodeStatus.FAILED,
            input_hash=key,
            duration_s=0.0,
            executor_used="code",
            model_used="-",
            effort_used="-",
            error=NodeError(type=ErrorType.EXECUTOR_ERROR, message="fell over", transient=True),
        )
        journal.append(ResultLine(key=key, record=record, at="2026-08-22T09:12:03+00:00"))
    return keys


@pytest.mark.os_agnostic
def test_run_retry_refuses_a_grant_the_relaunch_could_never_reach(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The retry ceiling comes from the policy resolved AT THE RELAUNCH, not the one the run
    started under. A grant naming an attempt beyond that ceiling folds and is never matched, and
    the write-once refusal then blocks a second grant - so refuse while the operator can still act.
    """
    run_id, _ex, obj = _started_with_a_failed_work_node(cli_runner, tmp_path)
    _append_failed_attempts(tmp_path / "runs" / run_id, "g_spent", range(3))  # shipped table allows 2

    result = cli_runner.invoke(
        cli_mod.cli, ["run", "retry", run_id, "g_spent", "--runs", str(tmp_path / "runs")], obj=obj
    )

    assert result.exit_code == ExitCode.INVALID_ARGUMENT
    assert "attempt 2" in result.output and "attempt 1" in result.output
    assert not list((tmp_path / "runs" / run_id / "retries").glob("g_spent.*.json"))


@pytest.mark.os_agnostic
def test_run_retry_allows_a_late_attempt_the_existing_grants_do_reach(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The guard is reachability, not the attempt number: a chain the recorded grants already
    carry past the automatic ceiling is grantable again."""
    run_id, _ex, obj = _started_with_a_failed_work_node(cli_runner, tmp_path)
    keys = _append_failed_attempts(tmp_path / "runs" / run_id, "g_spent", range(3))
    runs_arg = str(tmp_path / "runs")
    granted = cli_runner.invoke(
        cli_mod.cli, ["run", "retry", run_id, "g_spent", "--runs", runs_arg, "--no-relaunch"], obj=obj
    )
    assert granted.exit_code == ExitCode.INVALID_ARGUMENT  # attempt 2 is out of reach ...

    FsRunDir.open(tmp_path / "runs", run_id).write_retry_grant(
        RetryGrant(node_id="g_spent", key=keys[1], reason="", by="me", token_id="local")
    )  # ... until the step into it is granted

    result = cli_runner.invoke(
        cli_mod.cli, ["run", "retry", run_id, "g_spent", "--runs", runs_arg, "--no-relaunch"], obj=obj
    )

    assert result.exit_code == 0, result.output
