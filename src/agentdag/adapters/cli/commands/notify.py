"""``notify-test``: send one run event through the configured sink, and report it LOUDLY.

Everything else about notification is quiet by design.
:func:`~agentdag.application.kernel.notify.emit_best_effort` contains whatever a sink
raises, because a mail server being down is not a run failure and a finished run reported
as ``failed`` for want of a notification says the opposite of the truth. The cost of that
choice is a gap: a misconfigured sink behaves exactly like no sink at all, and nothing in
a run will ever say so.

This verb is the answer to that gap, and it works by being the ONE place the failure is
not contained. It resolves the sink exactly as a run does - the same
:func:`~agentdag.adapters.cli.commands.run.resolve_notifier`, so it cannot report healthy
what ``run start`` would refuse - then calls ``emit`` DIRECTLY rather than through
``emit_best_effort``, and turns whatever comes back into a message and an exit code.

Put it where the operator is standing: setting a sink up, at a terminal, able to fix it.
That is also this verb's limit, and it is worth stating - it proves the sink worked ONCE,
now. A credential that expires next week, or a host that goes away mid-run, is still
silent. Closing that would take a journal line for a failed emit, which is a schema change
and a decision, not an implementation detail.

Contents:
    * :func:`cli_notify_test` - the ``notify-test`` command.
"""

from __future__ import annotations

import rich_click as click

from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.application.kernel.notify import RunEvent
from agentdag.domain.models import RunStatus

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..exit_codes import ExitCode
from .run import resolve_notifier

__all__ = ["cli_notify_test"]

_TEST_EVENT_AT = "1970-01-01T00:00:00+00:00"
"""The stamp the probe event carries: a fixed, obviously-not-real instant.

Deliberately not the current time. The mail this sends is the only notification an
operator ever receives that describes no actual run, so it must not be mistakable for one
in a mailbox six months from now - a plausible timestamp is what would make it
mistakable.
"""


@click.command("notify-test", context_settings=CLICK_CONTEXT_SETTINGS)
@click.pass_context
def cli_notify_test(ctx: click.Context) -> None:
    """Send one test event through the sink kernel.notify names, and report what happened.

    Exits 0 when the sink accepted the event (or when none is configured, which is a
    correct answer rather than a failure), and non-zero when resolving or delivering it
    failed - so a setup script can branch on it.

    Raises:
        SystemExit: the sink could not be resolved (``kernel.notify`` names something
            unknown, or ``mail`` with no SMTP host - both refused by the same code a run
            uses), or delivery itself failed.
    """
    notifier = resolve_notifier(ctx)  # exits with a named reason if the config is not usable
    if isinstance(notifier, NoNotifier):
        safe_console.echo("kernel.notify is 'none': no notification is sent, so there is nothing to test.")
        return
    event = RunEvent(
        run_id="notify-test",
        workflow="notify-test",
        status=RunStatus.DONE,
        at=_TEST_EVENT_AT,
    )
    try:
        notifier.emit(event)
    except Exception as failed:
        safe_console.echo(f"the configured sink failed: {failed}", err=True)
        raise SystemExit(ExitCode.SMTP_FAILURE) from failed
    safe_console.echo("the configured sink accepted a test notification.")
