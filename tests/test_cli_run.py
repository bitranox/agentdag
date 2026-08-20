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
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from kernel_fakes import CommittingExecutor, fleet, git, policy_path

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.adapters.kernel.scope_none import NoScope
from agentdag.application.kernel.cancel import scope_unit
from agentdag.application.kernel.ports import KernelWiring, LaunchResult, ScopeHandle
from agentdag.composition import AppServices, build_production

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from click.testing import CliRunner

    from agentdag.application.kernel.ports import Scope


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
) -> Callable[[], AppServices]:
    """Return a services factory whose ``wire_kernel`` hands back a fixed wiring over real adapters.

    ``scope`` defaults to a real :class:`NoScope`; pass a :class:`RecordingScope` for a
    test that must exercise the BACKGROUND launch path without spawning a real process.
    ``wire_calls``, if given, is appended one dict of kwargs per ``wire_kernel`` call -
    for a test asserting what ``_build_wiring`` (fix round 1) resolved ``--parallel``/
    ``--policy`` to, across BOTH times ``run start --foreground`` calls it.
    """
    wiring = KernelWiring(
        journal_factory=JsonlJournal,
        lock=FileRunLock(),
        clock=UtcClock(),
        executors={"claude": executor},
        gate_port=MakeTestGate(command=(sys.executable, "-c", "raise SystemExit(0)")),
        git=GitCli(),
        scanner=IsolationScanner(),
        policy=load_policy(policy_path()),
        scope=scope if scope is not None else NoScope(),
        sandbox=NoSandbox(),
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
    scope = RecordingScope(cross_process_capable=True, is_alive_result=True, kill_result=kill_result)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=obj)
    match = re.search(r"run (\S+) suspended", started.output)
    assert match, started.output
    run_id = match.group(1)
    runs_arg = str(tmp_path / "runs")
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
