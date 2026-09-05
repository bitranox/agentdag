"""``[kernel] gate_command``: what the run's gate runs, resolved once and carried by the run.

The command is a kernel setting like ``parallel`` or ``deny_tools``, so it follows the same
route and is tested along the same one: ``run start`` resolves it from config and ``--set``,
writes it into ``state.json``, and every later launch - the background child above all, which
is a fresh process that loads config from files alone - builds its wiring from THAT and never
from whatever config it happens to see.

The blank-versus-empty rule differs from the denylists' on purpose and both arms are here: a
denylist that is empty denies nothing, which is a boundary an operator may widen deliberately,
while an argv that is empty is not a command at all, so ``[]`` is refused and a blank falls
back to the packaged default rather than leaving the run with no gate.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from kernel_fakes import CommittingExecutor
from test_cli_run import RecordingScope, services_with, start_args
from test_cli_run_settings import child_argv, settings_block, started_run_id, suspended_run_id

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from click.testing import CliRunner

_TRUE = ["--set", 'kernel.gate_command=["true"]']
"""A one-word gate command that is not the packaged default, so an arm asserting on it cannot
pass against code that still hard-codes ``make test``."""


def _wired_gate_commands(calls: Sequence[Mapping[str, object]]) -> list[tuple[str, ...]]:
    """The ``gate_command`` every ``wire_kernel`` call in ``calls`` was given."""
    return [tuple(cast("Sequence[str]", call["gate_command"])) for call in calls]


@pytest.mark.os_agnostic
def test_a_configured_gate_command_reaches_the_wiring(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``--set kernel.gate_command`` is what ``wire_kernel`` is asked to build the gate from."""
    (tmp_path / "runs").mkdir()
    calls: list[Mapping[str, object]] = []
    services = services_with(CommittingExecutor(), tmp_path, wire_calls=calls)

    result = cli_runner.invoke(cli_mod.cli, [*_TRUE, *start_args(tmp_path)], obj=services)

    assert result.exit_code == 0, result.output
    assert calls, "the run built no wiring at all"
    assert _wired_gate_commands(calls) == [("true",)] * len(calls), calls


@pytest.mark.os_agnostic
def test_an_unconfigured_gate_command_wires_the_packaged_default(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The DEFAULT path, which a test passing ``--set`` never exercises: the shipped ``make test``."""
    (tmp_path / "runs").mkdir()
    calls: list[Mapping[str, object]] = []
    services = services_with(CommittingExecutor(), tmp_path, wire_calls=calls)

    result = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=services)

    assert result.exit_code == 0, result.output
    assert calls, "the run built no wiring at all"
    assert _wired_gate_commands(calls) == [("make", "test")] * len(calls), calls


@pytest.mark.os_agnostic
def test_run_start_persists_the_gate_command_on_the_run(cli_runner: CliRunner, tmp_path: Path) -> None:
    """It is in ``state.json``: a relaunch must not have to re-read the config that typed it."""
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(confirm_alive=True)
    services = services_with(CommittingExecutor(), tmp_path, scope=scope)

    result = cli_runner.invoke(cli_mod.cli, [*_TRUE, *start_args(tmp_path, foreground=False)], obj=services)

    assert result.exit_code == 0, result.output
    assert settings_block(tmp_path, started_run_id(result.output))["gate_command"] == ["true"]


@pytest.mark.os_agnostic
def test_a_background_child_wires_the_run_s_gate_command_not_its_own_config(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The ``_coordinate`` argv the CLI built, replayed with NO ``--set``, still gates on ``true``.

    This is the arm that matters: a background child is a separate process whose config carries
    the packaged default, so a ``gate_command`` that did not travel on the run would leave the
    child running a DIFFERENT gate than the command that started it.
    """
    (tmp_path / "runs").mkdir()
    scope = RecordingScope(confirm_alive=True)
    parent = services_with(CommittingExecutor(), tmp_path, scope=scope)
    started = cli_runner.invoke(cli_mod.cli, [*_TRUE, *start_args(tmp_path, foreground=False)], obj=parent)
    assert started.exit_code == 0, started.output

    calls: list[Mapping[str, object]] = []
    child_services = services_with(CommittingExecutor(), tmp_path, wire_calls=calls)
    child = cli_runner.invoke(cli_mod.cli, child_argv(scope), obj=child_services)

    assert child.exit_code == 0, child.output
    assert calls, "the child built no wiring at all"
    assert _wired_gate_commands(calls) == [("true",)] * len(calls), calls


@pytest.mark.os_agnostic
def test_a_resume_keeps_the_gate_command_the_run_was_started_with(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A resume under a plain config gates on what the run carries, not on the packaged default."""
    (tmp_path / "runs").mkdir()
    started = cli_runner.invoke(
        cli_mod.cli, [*_TRUE, *start_args(tmp_path)], obj=services_with(CommittingExecutor(), tmp_path)
    )
    assert started.exit_code == 0, started.output
    run_id = suspended_run_id(started.output)

    calls: list[Mapping[str, object]] = []
    resume_argv = ["run", "resume", run_id, "--runs", str(tmp_path / "runs"), "--foreground"]
    resumed = cli_runner.invoke(
        cli_mod.cli, resume_argv, obj=services_with(CommittingExecutor(), tmp_path, wire_calls=calls)
    )

    assert resumed.exit_code == 0, resumed.output
    assert calls, "the resume built no wiring at all"
    assert _wired_gate_commands(calls) == [("true",)] * len(calls), calls


@pytest.mark.os_agnostic
def test_a_run_whose_settings_predate_the_gate_command_wires_the_default(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A ``settings`` block written before this field existed belonged to a run that gated on
    ``make test``, so that is what its relaunch wires - not a refusal, and not nothing."""
    (tmp_path / "runs").mkdir()
    started = cli_runner.invoke(cli_mod.cli, start_args(tmp_path), obj=services_with(CommittingExecutor(), tmp_path))
    assert started.exit_code == 0, started.output
    run_id = suspended_run_id(started.output)
    state_path = tmp_path / "runs" / run_id / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["settings"].pop("gate_command") == ["make", "test"], state  # the precondition: it WAS there
    state_path.write_text(json.dumps(state), encoding="utf-8")

    calls: list[Mapping[str, object]] = []
    resume_argv = ["run", "resume", run_id, "--runs", str(tmp_path / "runs"), "--foreground"]
    resumed = cli_runner.invoke(
        cli_mod.cli, resume_argv, obj=services_with(CommittingExecutor(), tmp_path, wire_calls=calls)
    )

    assert resumed.exit_code == 0, resumed.output
    assert "no settings block" not in resumed.output, resumed.output  # the BLOCK is there; only the field is not
    assert calls, "the resume built no wiring at all"
    assert _wired_gate_commands(calls) == [("make", "test")] * len(calls), calls


@pytest.mark.os_agnostic
def test_an_explicitly_empty_gate_command_is_refused_before_a_run_directory_exists(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """``[]`` is not a widened boundary the way an empty denylist is - it is not a command at all."""
    runs = tmp_path / "runs"
    runs.mkdir()
    argv = ["--set", "kernel.gate_command=[]", *start_args(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=services_with(CommittingExecutor(), tmp_path))

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert "kernel.gate_command" in result.output, result.output
    assert list(runs.iterdir()) == []  # refused BEFORE any run directory was created


@pytest.mark.os_agnostic
def test_a_blank_gate_command_falls_back_to_the_packaged_default(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A blank leaves the run gated, unlike a blank denylist, which would leave it unguarded."""
    (tmp_path / "runs").mkdir()
    calls: list[Mapping[str, object]] = []
    argv = ["--set", "kernel.gate_command=", *start_args(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=services_with(CommittingExecutor(), tmp_path, wire_calls=calls))

    assert result.exit_code == 0, result.output
    assert calls, "the run built no wiring at all"
    assert _wired_gate_commands(calls) == [("make", "test")] * len(calls), calls


@pytest.mark.os_agnostic
def test_a_gate_command_carrying_a_blank_word_is_refused(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A blank argv word is a mistake, and one the child process reports as a confusing usage error."""
    (tmp_path / "runs").mkdir()
    argv = ["--set", 'kernel.gate_command=["make", ""]', *start_args(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=services_with(CommittingExecutor(), tmp_path))

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert "kernel.gate_command" in result.output, result.output


@pytest.mark.os_agnostic
def test_a_gate_command_that_is_not_a_list_of_words_is_refused(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A number is not an argv; the layered config coerces one, so the refusal has to be explicit."""
    (tmp_path / "runs").mkdir()
    argv = ["--set", "kernel.gate_command=7", *start_args(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=services_with(CommittingExecutor(), tmp_path))

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert "kernel.gate_command" in result.output, result.output
    # The "close nothing on purpose" hint is correct for a denylist and WRONG here: an empty
    # gate_command is refused outright a few lines later, so stating it would tell an operator
    # to write the one value this same reader refuses by name.
    assert "close nothing on purpose" not in result.output, result.output


@pytest.mark.os_agnostic
def test_a_json_array_written_into_a_dotenv_is_refused_rather_than_split_into_nonsense(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A ``.env`` value is TEXT: unlike an ``AGENTDAG___`` variable or ``--set``, nothing parses it.

    So the JSON array an operator naturally writes arrives as one string and the comma split
    would turn it into the words ``["pytest"`` and ``"-q"]`` - an argv nobody wrote, from a value
    that looks right in the file.
    """
    (tmp_path / "runs").mkdir()
    env_file = tmp_path / "dotenv"
    env_file.write_text('KERNEL__GATE_COMMAND=["pytest","-q"]\n', encoding="utf-8")
    argv = ["--env-file", str(env_file), *start_args(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=services_with(CommittingExecutor(), tmp_path))

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert "kernel.gate_command" in result.output, result.output


@pytest.mark.os_agnostic
def test_an_empty_json_array_written_into_a_dotenv_is_refused_like_any_other(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The route that bypassed the empty-command refusal: ``[]`` in a ``.env`` is the STRING ``[]``.

    Read as one word it is a program named ``[]``, so the run starts, spends its work node, and
    the gate then dies on a missing executable - the outcome the refusal exists to prevent,
    reached through the one route that does not parse its value.
    """
    (tmp_path / "runs").mkdir()
    env_file = tmp_path / "dotenv"
    env_file.write_text("KERNEL__GATE_COMMAND=[]\n", encoding="utf-8")
    argv = ["--env-file", str(env_file), *start_args(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=services_with(CommittingExecutor(), tmp_path))

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert "kernel.gate_command" in result.output, result.output
    assert list((tmp_path / "runs").iterdir()) == []


@pytest.mark.os_agnostic
def test_a_gate_command_word_that_is_not_text_is_refused(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A null INSIDE the list is refused for the same reason a null instead of the list is.

    ``str(None)`` is ``"None"``, so without this the run would gate on ``make None``.
    """
    (tmp_path / "runs").mkdir()
    argv = ["--set", 'kernel.gate_command=["make", null]', *start_args(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=services_with(CommittingExecutor(), tmp_path))

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert "kernel.gate_command" in result.output, result.output


@pytest.mark.os_agnostic
def test_a_comma_joined_gate_command_from_an_env_style_override_is_split(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The env-var shape ``AGENTDAG___KERNEL__GATE_COMMAND=make,test`` arrives as one string."""
    (tmp_path / "runs").mkdir()
    calls: list[Mapping[str, object]] = []
    argv = ["--set", "kernel.gate_command=pytest, -q", *start_args(tmp_path)]

    result = cli_runner.invoke(cli_mod.cli, argv, obj=services_with(CommittingExecutor(), tmp_path, wire_calls=calls))

    assert result.exit_code == 0, result.output
    assert calls, "the run built no wiring at all"
    assert _wired_gate_commands(calls) == [("pytest", "-q")] * len(calls), calls
