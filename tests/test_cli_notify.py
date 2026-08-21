"""Tests for ``notify-test``: the verb that exercises the configured sink LOUDLY.

Everything else about notification is deliberately quiet - ``emit_best_effort`` contains
whatever a sink raises, because a mail server being down is not a run failure. That
leaves one gap, and this verb is the answer to it: an operator setting a sink up needs a
place where a failure is reported rather than swallowed. So the assertion that matters
here is the third one - a raising sink must produce a NON-ZERO exit and the error's text,
which is exactly what the same sink failing mid-run must NOT do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from lib_layered_config import Config

from agentdag.adapters.cli import root as cli_mod
from agentdag.adapters.cli.exit_codes import ExitCode
from agentdag.adapters.memory import load_email_config_from_dict_in_memory
from agentdag.composition import AppServices, build_production

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from agentdag.adapters.email.config import EmailConfig


MAIL_CONFIG: dict[str, Any] = {
    "kernel": {"notify": "mail"},
    "email": {"smtp_hosts": ["smtp.test.com:587"], "from_address": "runs@test.com", "recipients": ["op@test.com"]},
}


def services_over(config_data: dict[str, Any], send_notification: Any) -> Callable[[], AppServices]:
    """Build a services factory over a fixed config and a chosen notification sender.

    Both halves are injected at the seams production uses - ``get_config`` and the
    ``SendNotification`` port - so the command under test runs its real resolution path.
    """
    prod = build_production()
    config = Config(config_data, {})

    def _fake_get_config(**_kwargs: Any) -> Config:
        return config

    services = AppServices(
        get_config=_fake_get_config,
        get_default_config_path=prod.get_default_config_path,
        deploy_configuration=prod.deploy_configuration,
        display_config=prod.display_config,
        send_email=prod.send_email,
        send_notification=send_notification,
        load_email_config_from_dict=load_email_config_from_dict_in_memory,
        init_logging=prod.init_logging,
        wire_graph_a=prod.wire_graph_a,
        wire_kernel=prod.wire_kernel,
    )
    return lambda: services


def refusing_sender(*, message: str) -> Any:
    """Return a ``send_notification`` that always raises ``message``, like an unreachable host."""

    def _send(**_kwargs: Any) -> bool:
        raise RuntimeError(message)

    return _send


@pytest.fixture
def cli() -> CliRunner:
    """A fresh runner per test."""
    return CliRunner()


@pytest.mark.os_agnostic
def test_notify_test_says_plainly_that_a_none_sink_sends_nothing(cli: CliRunner) -> None:
    sent: list[str] = []

    def _send(*, subject: str, **_kwargs: Any) -> bool:
        sent.append(subject)
        return True

    obj = services_over({"kernel": {"notify": "none"}}, _send)

    result = cli.invoke(cli_mod.cli, ["notify-test"], obj=obj)

    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert "none" in result.output
    assert sent == []  # nothing configured, so nothing sent - and it says so rather than claiming success


@pytest.mark.os_agnostic
def test_notify_test_sends_exactly_one_notification_through_the_configured_sink(cli: CliRunner) -> None:
    sent: list[dict[str, object]] = []

    def _send(
        *,
        config: EmailConfig,
        subject: str,
        message: str,
        recipients: str | Sequence[str] | None = None,
        from_address: str | None = None,
    ) -> bool:
        del from_address
        sent.append({"config": config, "subject": subject, "message": message, "recipients": recipients})
        return True

    obj = services_over(MAIL_CONFIG, _send)

    result = cli.invoke(cli_mod.cli, ["notify-test"], obj=obj)

    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert len(sent) == 1
    assert "agentdag" in str(sent[0]["subject"])


@pytest.mark.os_agnostic
def test_notify_test_reports_a_failing_sink_loudly_instead_of_containing_it(cli: CliRunner) -> None:
    # THE reason this verb exists. The same failure reaching emit_best_effort mid-run is
    # swallowed on purpose; here it must reach the operator, with a non-zero exit so a
    # script driving the setup can tell.
    obj = services_over(MAIL_CONFIG, refusing_sender(message="no route to host"))

    result = cli.invoke(cli_mod.cli, ["notify-test"], obj=obj)

    assert result.exit_code != ExitCode.SUCCESS
    assert "no route to host" in result.output


@pytest.mark.os_agnostic
def test_notify_test_refuses_mail_with_no_smtp_host_the_same_way_a_run_does(cli: CliRunner) -> None:
    # One resolution path, so the verb cannot report a sink healthy that `run start` refuses.
    obj = services_over({"kernel": {"notify": "mail"}, "email": {"smtp_hosts": []}}, refusing_sender(message="unused"))

    result = cli.invoke(cli_mod.cli, ["notify-test"], obj=obj)

    assert result.exit_code != ExitCode.SUCCESS
    assert "smtp_hosts" in result.output


@pytest.mark.os_agnostic
def test_notify_test_refuses_an_unknown_sink_name(cli: CliRunner) -> None:
    obj = services_over({"kernel": {"notify": "carrier-pigeon"}}, refusing_sender(message="unused"))

    result = cli.invoke(cli_mod.cli, ["notify-test"], obj=obj)

    assert result.exit_code != ExitCode.SUCCESS
    assert "carrier-pigeon" in result.output
