"""The two denylists that make up the kernel's stated boundary, read from config and refused by name.

``[kernel] deny_bash`` filters Bash commands; ``[kernel] deny_tools`` closes the tools that reach
the network or spawn sub-agents (``WebFetch``, ``WebSearch``, ``Task`` by shipped default). Both
are lists; both fail CLOSED on a blank value rather than silently becoming an empty list. The
distinction that matters here is between a BLANK - an env var set to nothing, ``--set
kernel.deny_bash=`` - which is a misconfiguration and is refused before any run directory exists,
and an EXPLICIT empty list, ``[]``, which is an operator stating that this run widens the boundary
and is honoured as such.

The tests drive the real CLI over the real config path and read what ``_build_wiring`` handed to
``wire_kernel``, never a return value.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from test_cli_run import CommittingExecutor, services_with, start_args

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from click.testing import CliRunner

SHIPPED_DENY_TOOLS = ("WebFetch", "WebSearch", "Task")
"""The packaged default of ``[kernel] deny_tools``, asserted by VALUE."""


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


@pytest.fixture
def blank_deny_bash_in_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Set the env-var override to nothing, around a cleared config cache on BOTH sides.

    The shared ``clear_config_cache`` fixture clears only before a test; a config loaded
    while this env var is set would otherwise stay cached and reach every later test.
    """
    from agentdag.adapters.config import loader as config_mod

    monkeypatch.setenv("AGENTDAG___KERNEL__DENY_BASH", "")
    config_mod.get_config.cache_clear()
    yield
    config_mod.get_config.cache_clear()


@pytest.mark.os_agnostic
def test_a_json_array_written_into_a_dotenv_is_refused_rather_than_read_as_one_entry(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A ``.env`` value is TEXT, and this list fails OPEN when that is not noticed.

    ``AGENTDAG___KERNEL__DENY_BASH='["git push"]'`` is parsed as a list; the same words in a
    ``.env`` are not, so they used to become the single substring ``["git push"]`` - a denylist
    that matches that literal and NOT ``git push``, which reads as closed in the file and is
    open in the run.
    """
    env_file = tmp_path / "dotenv"
    env_file.write_text('KERNEL__DENY_BASH=["git push"]\n', encoding="utf-8")

    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--env-file", str(env_file)])

    _assert_refused_by_name(rc, output, "kernel.deny_bash", tmp_path / "runs")


@pytest.mark.os_agnostic
def test_a_blank_deny_bash_env_var_is_refused_not_read_as_no_denylist(
    cli_runner: CliRunner, tmp_path: Path, blank_deny_bash_in_env: None
) -> None:
    """The measured defect: an env var set to nothing used to reach the hook as ``()``."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=[])

    _assert_refused_by_name(rc, output, "kernel.deny_bash", tmp_path / "runs")


@pytest.mark.os_agnostic
def test_a_blank_pattern_inside_deny_bash_is_refused(cli_runner: CliRunner, tmp_path: Path) -> None:
    """An empty substring matches every command, so a blank entry is a misconfiguration, not a pattern."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--set", 'kernel.deny_bash=["git push", "  "]'])

    _assert_refused_by_name(rc, output, "kernel.deny_bash", tmp_path / "runs")


@pytest.mark.os_agnostic
def test_deny_tools_ships_closed_to_the_network_and_to_sub_agents(cli_runner: CliRunner, tmp_path: Path) -> None:
    """With nothing configured, the wiring is told to deny exactly the shipped three."""
    rc, output, calls = _start(cli_runner, tmp_path, set_args=[])

    assert rc == 0, output
    assert calls and calls[0]["deny_tools"] == SHIPPED_DENY_TOOLS


@pytest.mark.os_agnostic
def test_an_explicit_empty_deny_tools_is_an_operator_choice_and_is_honoured(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """``[]`` is a statement, not a blank: the run starts and the wiring is told to deny no tool."""
    rc, output, calls = _start(cli_runner, tmp_path, set_args=["--set", "kernel.deny_tools=[]"])

    assert rc == 0, output
    assert calls and calls[0]["deny_tools"] == ()


@pytest.mark.os_agnostic
def test_a_blank_deny_tools_is_refused_by_name(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The same blank-versus-empty rule as the Bash denylist."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--set", "kernel.deny_tools="])

    _assert_refused_by_name(rc, output, "kernel.deny_tools", tmp_path / "runs")


@pytest.mark.os_agnostic
def test_a_hyphenated_mcp_tool_name_is_accepted(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The CLI's exact-string matcher class includes ``-``, and MCP tools are commonly named with it."""
    name = "mcp__context7__resolve-library-id"
    rc, output, calls = _start(cli_runner, tmp_path, set_args=["--set", f"kernel.deny_tools={json.dumps([name])}"])

    assert rc == 0, output
    assert calls and calls[0]["deny_tools"] == (name,)


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("kernel.deny_tools", "null"),
        ("kernel.deny_tools", "0"),
        ("kernel.deny_bash", "true"),
        ("kernel.deny_tools", json.dumps({"WebFetch": 1})),
    ],
    ids=["null", "int", "bool", "mapping"],
)
def test_a_value_that_is_neither_a_list_nor_a_string_is_refused_by_name(
    cli_runner: CliRunner, tmp_path: Path, key: str, value: str
) -> None:
    """A null, a number, a bool or a mapping is refused naming the key - never a traceback, never a mapping's keys."""
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=["--set", f"{key}={value}"])

    _assert_refused_by_name(rc, output, key, tmp_path / "runs")
    assert "Traceback" not in output


@pytest.mark.os_agnostic
def test_a_deny_tools_entry_that_cannot_be_a_tool_name_is_refused(cli_runner: CliRunner, tmp_path: Path) -> None:
    """A matcher never fires on a name with a space in it, so the typo would widen the boundary silently."""
    set_args = ["--set", f"kernel.deny_tools={json.dumps(['Web Fetch'])}"]
    rc, output, _calls = _start(cli_runner, tmp_path, set_args=set_args)

    _assert_refused_by_name(rc, output, "kernel.deny_tools", tmp_path / "runs")
