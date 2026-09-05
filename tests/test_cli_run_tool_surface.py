"""The tool surface a run hands its nodes: ``[kernel] tools`` and ``[kernel] permission_mode``.

``tools`` is the list the executor passes as the SDK's ``allowed_tools``, and ``permission_mode``
is the mode it dispatches under. Neither CLOSES anything: ``allowed_tools`` is an auto-approval
list, so widening it removes prompts rather than adding reach, and the PreToolUse deny hooks
(``deny_bash``, ``deny_tools``, the write-set and read-confinement hooks) are what actually bound
a node in every mode.

Both values are read once by ``run start`` and carried on the run, so the arms here drive the real
CLI over the real config path and read what ``_build_wiring`` handed to ``wire_kernel``, the same
way ``test_cli_run_denylists.py`` does.

The empty and absent cases are decided differently from the denylists, and deliberately: an
absent value is the packaged one, a BLANK is refused by name, and ``tools = []`` is refused by
name too - a run whose every node may auto-approve no tool at all pays for a dispatch that can
only emit text.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from test_cli_run import CommittingExecutor, services_with, start_args

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    from click.testing import CliRunner

SHIPPED_TOOLS = ("Read", "Edit", "Write", "Bash", "Grep", "Glob")
"""The packaged default of ``[kernel] tools``, asserted by VALUE."""

SHIPPED_PERMISSION_MODE = "dontAsk"
"""The packaged default of ``[kernel] permission_mode``, asserted by VALUE."""


def _start(
    cli_runner: CliRunner, tmp_path: Path, *, set_args: list[str]
) -> tuple[int, str, list[Mapping[str, object]]]:
    """Run ``run start graph-a --foreground`` under ``set_args`` and return ``(rc, output, wire_kernel kwargs)``."""
    (tmp_path / "runs").mkdir()
    calls: list[Mapping[str, object]] = []
    obj = services_with(CommittingExecutor(), tmp_path, wire_calls=calls)
    result = cli_runner.invoke(cli_mod.cli, [*set_args, *start_args(tmp_path)], obj=obj)
    return result.exit_code, result.output, calls


def _assert_refused_by_name(rc: int, output: str, key: str, runs: Path) -> None:
    """The refusal names the config key and leaves no run directory behind."""
    assert rc == ExitCode.INVALID_ARGUMENT, output
    assert key in output, output
    assert list(runs.iterdir()) == []


def _tools_of(calls: Sequence[Mapping[str, object]]) -> list[tuple[str, ...]]:
    """The ``tools`` every ``wire_kernel`` call in ``calls`` was given."""
    return [tuple(cast("Sequence[str]", call["tools"])) for call in calls]


@pytest.fixture
def blank_tools_in_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Set the env-var override to nothing, around a cleared config cache on BOTH sides.

    The shared ``clear_config_cache`` fixture clears only before a test; a config loaded
    while this env var is set would otherwise stay cached and reach every later test.
    """
    from agentdag.adapters.config import loader as config_mod

    monkeypatch.setenv("AGENTDAG___KERNEL__TOOLS", "")
    config_mod.get_config.cache_clear()
    yield
    config_mod.get_config.cache_clear()


@pytest.mark.os_agnostic
def test_the_shipped_tool_surface_is_the_six_tools_under_dont_ask(cli_runner: CliRunner, tmp_path: Path) -> None:
    """With nothing configured, the wiring is told the packaged tool set and the packaged mode."""
    rc, output, calls = _start(cli_runner, tmp_path, set_args=[])

    assert rc == 0, output
    assert calls and calls[0]["tools"] == SHIPPED_TOOLS
    assert calls[0]["permission_mode"] == SHIPPED_PERMISSION_MODE


@pytest.mark.os_agnostic
def test_a_configured_tool_set_is_what_the_wiring_is_built_from(cli_runner: CliRunner, tmp_path: Path) -> None:
    """``--set kernel.tools`` reaches every ``wire_kernel`` call, not just the process that typed it."""
    wanted = ["Read", "Grep", "Task", "mcp__context7__resolve-library-id"]
    set_args = ["--set", f"kernel.tools={json.dumps(wanted)}"]

    rc, output, calls = _start(cli_runner, tmp_path, set_args=set_args)

    assert rc == 0, output
    assert calls and _tools_of(calls) == [tuple(wanted)] * len(calls)


@pytest.mark.os_agnostic
def test_bypass_permissions_is_a_mode_an_operator_may_choose(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The one widening mode that still runs unattended, and whose deny hooks still fire."""
    set_args = ["--set", "kernel.permission_mode=bypassPermissions"]

    rc, output, calls = _start(cli_runner, tmp_path, set_args=set_args)

    assert rc == 0, output
    assert calls and [call["permission_mode"] for call in calls] == ["bypassPermissions"] * len(calls)


@pytest.mark.os_agnostic
def test_an_explicitly_empty_tool_set_is_refused_by_name(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A run whose nodes auto-approve no tool can only emit text, and pays a full dispatch to find out."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--set", "kernel.tools=[]"])

    _assert_refused_by_name(rc, output, "kernel.tools", tmp_path / "runs")


@pytest.mark.os_agnostic
def test_a_blank_tools_value_is_refused_by_name(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A blank is a misconfiguration, never a silent fall back to the packaged set."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--set", "kernel.tools="])

    _assert_refused_by_name(rc, output, "kernel.tools", tmp_path / "runs")


@pytest.mark.os_agnostic
def test_a_blank_tools_env_var_is_refused_by_name(
    cli_runner: CliRunner, tmp_path: Path, blank_tools_in_env: None
) -> None:
    """The env-var route to a blank is the one that reaches a run nobody typed a value for."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=[])

    _assert_refused_by_name(rc, output, "kernel.tools", tmp_path / "runs")


@pytest.mark.os_agnostic
def test_a_tools_entry_that_cannot_be_a_tool_name_is_refused(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The names are joined with commas into one ``--allowedTools``, so a comma or space inside one splits it.

    Also pins the CONSEQUENCE the refusal states. The reader is shared with the denylists, where a
    bad entry matches EVERYTHING; here it matches nothing, and an operator told the denylist's
    consequence would go looking for a boundary that is too wide instead of one that is absent.
    """
    set_args = ["--set", f"kernel.tools={json.dumps(['Read', 'Web Fetch'])}"]

    rc, output, _calls = _start(cli_runner, tmp_path, set_args=set_args)

    _assert_refused_by_name(rc, output, "kernel.tools", tmp_path / "runs")
    assert "names no tool" in output, output
    assert "match every" not in output, output


@pytest.mark.os_agnostic
def test_a_json_array_written_into_a_dotenv_is_refused_rather_than_read_as_one_tool(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A ``.env`` value is TEXT: read as one word it would name a tool that does not exist."""
    env_file = tmp_path / "dotenv"
    env_file.write_text('KERNEL__TOOLS=["Read"]\n', encoding="utf-8")

    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--env-file", str(env_file)])

    _assert_refused_by_name(rc, output, "kernel.tools", tmp_path / "runs")


@pytest.mark.os_agnostic
def test_a_tools_value_that_is_not_a_list_does_not_offer_the_empty_list_as_a_choice(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The denylists tell an operator ``[]`` closes nothing on purpose; here ``[]`` is refused, so it must not."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--set", "kernel.tools=true"])

    _assert_refused_by_name(rc, output, "kernel.tools", tmp_path / "runs")
    assert "close nothing on purpose" not in output, output


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    "mode",
    ["plan", "default", "acceptEdits", "auto", "dontask", "bogus"],
    ids=["plan", "default", "acceptEdits", "auto", "wrong-case", "unknown"],
)
def test_a_permission_mode_an_unattended_run_cannot_use_is_refused_by_name(
    cli_runner: CliRunner, tmp_path: Path, mode: str
) -> None:
    """Only the two modes that decide every call without a person or a classifier are offered.

    ``plan`` executes no tool at all; ``default`` and ``acceptEdits`` fall back to a prompt an
    unattended session has nobody to answer; ``auto`` routes the decision to a model classifier,
    which is the one thing this coordinator exists not to branch on.
    """
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--set", f"kernel.permission_mode={mode}"])

    _assert_refused_by_name(rc, output, "kernel.permission_mode", tmp_path / "runs")
    assert "Traceback" not in output


@pytest.mark.os_agnostic
def test_a_blank_permission_mode_is_refused_by_name(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A blank never silently becomes the packaged mode: which mode a run dispatched under is recorded."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--set", "kernel.permission_mode="])

    _assert_refused_by_name(rc, output, "kernel.permission_mode", tmp_path / "runs")
