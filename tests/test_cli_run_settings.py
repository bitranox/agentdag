"""A run carries the kernel settings it was started with.

A background launch's coordinator is a fresh process that re-enters the CLI and loads config
from its own cwd, so a ``[kernel]`` value given by env, ``--set`` or ``--profile`` used to bind
only the LAUNCHING command: the child validated nothing and dispatched with the packaged list.
``run start`` now persists the RESOLVED settings on the run - ``state.json``'s ``settings``
block - and every relaunch path (the hidden ``_coordinate``, ``resume``, ``approve``, ``retry``)
builds its wiring from that block, never from the config of whoever happens to relaunch it.

The background arms REPLAY the exact argv the CLI built for its child through the real parser,
with none of the parent's ``--set`` flags: that argv crosses an argument-validation boundary an
in-process ``--foreground`` test never reaches, and the child has only what the run carries.
"""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING, cast

import pytest
from kernel_fakes import CommittingExecutor, policy_path
from test_cli_run import RecordingScope, services_with, start_args

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from click.testing import CliRunner


def _started_run_id(output: str) -> str:
    """The run id a BACKGROUND ``run start`` printed."""
    match = re.search(r"run (\S+) started \(unit", output)
    assert match, output
    return match.group(1)


def _suspended_run_id(output: str) -> str:
    """The run id a FOREGROUND graph-a run printed when it reached its approve."""
    match = re.search(r"run (\S+) suspended at a_push_list", output)
    assert match, output
    return match.group(1)


def _child_argv(scope: RecordingScope) -> list[str]:
    """The argv the CLI handed its background child, minus the interpreter prefix.

    Asserts the prefix is the one ``_launch_background`` builds, so a test replays what the
    child would parse and not a guess at it.
    """
    assert len(scope.calls) == 1, scope.calls
    argv = list(scope.calls[0].argv)
    assert argv[:3] == [sys.executable, "-m", "agentdag"], argv
    return argv[3:]


def _state(tmp_path: Path, run_id: str) -> dict[str, object]:
    """``state.json`` as written, read raw so the test pins the on-disk shape."""
    return json.loads((tmp_path / "runs" / run_id / "state.json").read_text(encoding="utf-8"))


def _settings(tmp_path: Path, run_id: str) -> dict[str, object]:
    """The persisted ``settings`` block, or a failure naming its absence."""
    state = _state(tmp_path, run_id)
    assert "settings" in state, state
    return cast("dict[str, object]", state["settings"])


def _run_started_by(tmp_path: Path, run_id: str) -> str:
    """The ``by`` of the run's single ``run_started`` journal line."""
    lines = (tmp_path / "runs" / run_id / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    started = [json.loads(line) for line in lines if json.loads(line)["event"] == "run_started"]
    assert len(started) == 1, started
    return str(started[0]["by"])


@pytest.mark.os_agnostic
def test_run_start_persists_the_resolved_kernel_settings_on_the_run(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``run start`` writes the settings it resolved - config, ``--set``, ``--policy`` - into ``state.json``."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(confirm_alive=True)
    obj = services_with(CommittingExecutor(), tmp_path, scope=scope)
    alt_policy = policy_path()  # a real, existing file: the VALUE PERSISTED is under test, not its content
    set_args = [
        "--set",
        "kernel.parallel=3",
        "--set",
        "kernel.max_turns=7",
        "--set",
        "kernel.default_node_tokens=1234",
        "--set",
        'kernel.deny_bash=["git push"]',
        "--set",
        "kernel.deny_tools=[]",
    ]
    argv = [*set_args, *start_args(tmp_path, foreground=False, extra=["--policy", str(alt_policy)])]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=obj)

    assert result.exit_code == 0, result.output
    state = _state(tmp_path, _started_run_id(result.output))
    assert state["settings"] == {
        "policy_path": str(alt_policy),
        "parallel": 3,
        "max_turns": 7,
        "default_node_tokens": 1234,
        "deny_bash": ["git push"],
        "deny_tools": [],
        "notify": "none",
        "credential_file": "",
    }


@pytest.mark.os_agnostic
def test_a_background_child_builds_its_wiring_from_the_run_not_from_its_own_config(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The ``_coordinate`` argv the CLI built, replayed with NO ``--set``, wires what the run carries."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(confirm_alive=True)
    parent = services_with(CommittingExecutor(), tmp_path, scope=scope)
    alt_policy = policy_path()
    set_args = ["--set", "kernel.parallel=3", "--set", "kernel.deny_tools=[]"]
    argv = [*set_args, *start_args(tmp_path, foreground=False, extra=["--policy", str(alt_policy)])]
    started = cli_runner.invoke(cli_mod.cli, argv, obj=parent)
    assert started.exit_code == 0, started.output

    calls: list[Mapping[str, object]] = []
    child_services = services_with(CommittingExecutor(), tmp_path, wire_calls=calls)
    child = cli_runner.invoke(cli_mod.cli, _child_argv(scope), obj=child_services)

    assert child.exit_code == 0, child.output
    assert calls, "the child built no wiring at all"
    assert [call["parallel"] for call in calls] == [3] * len(calls), calls
    assert [tuple(cast("Sequence[str]", call["deny_tools"])) for call in calls] == [()] * len(calls), calls
    assert [call["policy_path"] for call in calls] == [alt_policy] * len(calls), calls


@pytest.mark.os_agnostic
def test_a_background_child_records_the_run_s_operator_as_the_run_started_actor(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """``run_started``'s ``by`` in the child is the label the run was started under, not the child's config."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(confirm_alive=True)
    parent = services_with(CommittingExecutor(), tmp_path, scope=scope)
    set_args = ["--set", "kernel.operator=ops-desk"]
    started = cli_runner.invoke(cli_mod.cli, [*set_args, *start_args(tmp_path, foreground=False)], obj=parent)
    assert started.exit_code == 0, started.output
    run_id = _started_run_id(started.output)

    child = cli_runner.invoke(cli_mod.cli, _child_argv(scope), obj=services_with(CommittingExecutor(), tmp_path))

    assert child.exit_code == 0, child.output
    assert (_state(tmp_path, run_id)["owner"], _run_started_by(tmp_path, run_id)) == ("ops-desk", "ops-desk")


@pytest.mark.os_agnostic
def test_resume_builds_its_wiring_from_the_run_s_settings_not_the_current_config(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A resume under a plain config keeps the ``--set`` the run was started with."""
    (tmp_path / "runs").mkdir()
    started = cli_runner.invoke(
        cli_mod.cli,
        ["--set", "kernel.parallel=3", *start_args(tmp_path)],
        obj=services_with(CommittingExecutor(), tmp_path),
    )
    assert started.exit_code == 0, started.output
    run_id = _suspended_run_id(started.output)

    calls: list[Mapping[str, object]] = []
    resume_services = services_with(CommittingExecutor(), tmp_path, wire_calls=calls)
    resume_argv = ["run", "resume", run_id, "--runs", str(tmp_path / "runs"), "--foreground"]
    resumed = cli_runner.invoke(cli_mod.cli, resume_argv, obj=resume_services)

    assert resumed.exit_code == 0, resumed.output
    assert calls, "the resume built no wiring at all"
    assert [call["parallel"] for call in calls] == [3] * len(calls), calls


@pytest.mark.os_agnostic
def test_a_run_written_before_settings_existed_resolves_them_from_config_and_says_so(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A ``state.json`` with no ``settings`` block falls back to config, and the relaunch names that."""
    (tmp_path / "runs").mkdir()
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=services_with(CommittingExecutor(), tmp_path))
    assert started.exit_code == 0, started.output
    run_id = _suspended_run_id(started.output)
    state_path = tmp_path / "runs" / run_id / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "settings" in state, state  # the precondition: the block IS there before this strips it
    del state["settings"]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    calls: list[Mapping[str, object]] = []
    resume_services = services_with(CommittingExecutor(), tmp_path, wire_calls=calls)
    resume_argv = ["run", "resume", run_id, "--runs", str(tmp_path / "runs"), "--foreground"]
    resumed = cli_runner.invoke(cli_mod.cli, ["--set", "kernel.parallel=5", *resume_argv], obj=resume_services)

    assert resumed.exit_code == 0, resumed.output
    assert "no settings block" in resumed.output, resumed.output
    assert [call["parallel"] for call in calls] == [5] * len(calls), calls


@pytest.mark.os_agnostic
def test_a_persisted_credential_keyfile_that_is_gone_refuses_the_relaunch_by_name(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A run started on a keyfile does not silently fall back to a credential copy when the file is gone."""
    (tmp_path / "runs").mkdir()
    keyfile = tmp_path / "claude-oauth-token"
    keyfile.write_text("not-a-real-token\n", encoding="utf-8")
    set_args = ["--set", f"credentials.claude_oauth_token_file={keyfile}"]
    started = cli_runner.invoke(
        cli_mod.cli, [*set_args, *start_args(tmp_path)], obj=services_with(CommittingExecutor(), tmp_path)
    )
    assert started.exit_code == 0, started.output
    run_id = _suspended_run_id(started.output)
    assert _settings(tmp_path, run_id)["credential_file"] == str(keyfile)
    keyfile.unlink()

    resume_argv = ["run", "resume", run_id, "--runs", str(tmp_path / "runs"), "--foreground"]
    resumed = cli_runner.invoke(cli_mod.cli, resume_argv, obj=services_with(CommittingExecutor(), tmp_path))

    assert resumed.exit_code == ExitCode.INVALID_ARGUMENT, resumed.output
    assert str(keyfile) in resumed.output, resumed.output
