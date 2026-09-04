"""The actor a run records is a configured label, never the operating account's name.

Every run directory names its actor twice over: ``state.json`` carries the run's ``owner``, and
each journal line that records who did something (``run_started``, a resume, a retry grant, an
approve decision, a cancel) carries it as ``by``. A run directory copied out as evidence
publishes whatever sits there, and nothing in agentdag warns. So the value is ``[kernel]
operator`` from the layered config, whose packaged default is a non-identifying constant; an
operator who wants names in run directories sets one, knowingly.

These tests drive the real CLI over the real config path (``--set`` is the root option every
layer above the defaults could equally have supplied), and read the values back from the files
a run leaves behind, never from a return value.
"""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from test_cli_run import CommittingExecutor, services_with, start_args

from agentdag.adapters import cli as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode
from agentdag.application.kernel.approve import SYSTEM_IDENTITY

if TYPE_CHECKING:
    from click.testing import CliRunner

DEFAULT_LABEL = "operator"
"""The packaged default of ``[kernel] operator``, asserted by VALUE so a regression to the OS
user cannot pass on a machine whose account happens to carry this name (see the skip below)."""

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "agentdag"

USERNAME_READS = re.compile(r"getpass\.getuser\(|os\.getlogin\(|getpwuid\(")
"""Every stdlib route to the operating account's name."""


def _recorded_actors(runs: Path, run_id: str) -> tuple[str, str]:
    """Return ``(state owner, run_started by)`` as the run directory recorded them."""
    run_dir = runs / run_id
    owner = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))["owner"]
    started = [
        json.loads(line)
        for line in (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event"] == "run_started"
    ]
    assert len(started) == 1, started
    return owner, started[0]["by"]


def _start_suspended_run(cli_runner: CliRunner, tmp_path: Path, *, set_args: list[str]) -> tuple[str, object]:
    """Start graph-a in the foreground up to its approve, returning ``(run_id, services obj)``."""
    (tmp_path / "runs").mkdir()
    obj = services_with(CommittingExecutor(), tmp_path)
    started = cli_runner.invoke(cli_mod.cli, [*set_args, *start_args(tmp_path)], obj=obj)
    assert started.exit_code == 0, started.output
    match = re.search(r"run (\S+) suspended at a_push_list", started.output)
    assert match, started.output
    return match.group(1), obj


@pytest.mark.os_agnostic
def test_run_start_records_the_default_operator_label_not_the_os_user(cli_runner: CliRunner, tmp_path: Path) -> None:
    """With nothing configured, the run's owner and its ``run_started`` actor are the packaged constant."""
    if getpass.getuser() == DEFAULT_LABEL:
        pytest.skip("this account is literally named like the default label, so the arm cannot discriminate here")
    run_id, _obj = _start_suspended_run(cli_runner, tmp_path, set_args=[])

    owner, by = _recorded_actors(tmp_path / "runs", run_id)

    assert (owner, by) == (DEFAULT_LABEL, DEFAULT_LABEL)


@pytest.mark.os_agnostic
def test_a_configured_operator_label_reaches_the_owner_the_journal_and_a_decision(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """``[kernel] operator`` is what every actor slot records: the run's owner, ``run_started``, and an approve."""
    set_args = ["--set", "kernel.operator=ops-desk"]
    run_id, obj = _start_suspended_run(cli_runner, tmp_path, set_args=set_args)
    runs = tmp_path / "runs"

    approve_args = ["run", "approve", run_id, "a_push_list", "--decision", "hold", "--runs", str(runs), "--no-relaunch"]
    approved = cli_runner.invoke(cli_mod.cli, [*set_args, *approve_args], obj=obj)
    assert approved.exit_code == 0, approved.output

    owner, by = _recorded_actors(runs, run_id)
    decisions = sorted((runs / run_id / "decisions").glob("*.json"))
    assert len(decisions) == 1, decisions
    decided_by = json.loads(decisions[0].read_text(encoding="utf-8"))["by"]
    assert (owner, by, decided_by) == ("ops-desk", "ops-desk", "ops-desk")


@pytest.mark.os_agnostic
@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_a_blank_operator_label_is_refused_before_any_run_directory_exists(
    cli_runner: CliRunner, tmp_path: Path, blank: str
) -> None:
    """An explicit blank is a misconfiguration, refused up front and by name - not a silent fallback."""
    (tmp_path / "runs").mkdir()
    obj = services_with(CommittingExecutor(), tmp_path)

    result = cli_runner.invoke(cli_mod.cli, ["--set", f"kernel.operator={blank}", *start_args(tmp_path)], obj=obj)

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert "kernel.operator" in result.output
    assert list((tmp_path / "runs").iterdir()) == []


@pytest.mark.os_agnostic
def test_the_system_identity_is_refused_as_an_operator_label(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The run summary answers "was a human involved" by ``by != "system"``, so that word is not a label."""
    (tmp_path / "runs").mkdir()
    obj = services_with(CommittingExecutor(), tmp_path)

    set_arg = ["--set", f"kernel.operator={SYSTEM_IDENTITY}"]
    result = cli_runner.invoke(cli_mod.cli, [*set_arg, *start_args(tmp_path)], obj=obj)

    assert result.exit_code == ExitCode.INVALID_ARGUMENT, result.output
    assert "kernel.operator" in result.output and SYSTEM_IDENTITY in result.output
    assert list((tmp_path / "runs").iterdir()) == []


@pytest.mark.os_agnostic
def test_no_production_module_reads_the_operating_account_name() -> None:
    """The label is the ONLY actor source; a new call site that reads the OS user would fail silently otherwise.

    Forgetting to use the configured label is invisible at runtime - the run works, and the
    name leaks - so the shape is closed here rather than at each call site.
    """
    hits = [
        f"{path.relative_to(SRC_ROOT.parent.parent)}:{number}: {line.strip()}"
        for path in sorted(SRC_ROOT.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if USERNAME_READS.search(line)
    ]
    assert not hits, "production code reads the operating account's name:\n" + "\n".join(hits)
