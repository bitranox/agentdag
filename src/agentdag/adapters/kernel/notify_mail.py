"""The mail notification sink: a run event becomes one plain-text mail to the operator.

Delivery is not reimplemented here. The repo already ships an email adapter over
``btx_lib_mail`` (``agentdag.adapters.email``), and its ``send_notification`` is exactly
this shape - a subject, a message, a config - so this module is only the RENDERING: which
of a run's four events becomes which sentence.

Whose channel this is matters, and it is the reason the sink lives beside the kernel
rather than inside a workflow. Mail sent from here is the OPERATOR's channel about the
run - it finished, it broke, it is waiting on you - not an effect the workflow has on the
world. A workflow's effects go through ``stage``/``apply`` so they survive a crash exactly
once; a notification deliberately does not, because re-telling somebody a run finished is
harmless and never telling them is the failure worth avoiding.

Contents:
    * :class:`MailNotifier` - render a run event and hand it to the email adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...domain.models import RunStatus

if TYPE_CHECKING:
    from ...application.kernel.notify import RunEvent
    from ...application.ports import SendNotification
    from ..email.config import EmailConfig

__all__ = ["MailNotifier"]

_HEADLINE = {
    RunStatus.SUSPENDED: "is waiting for a decision",
    RunStatus.DONE: "finished",
    RunStatus.FAILED: "failed",
    RunStatus.CRASHED: "crashed",
}
"""What each status says in a subject line, in the operator's words rather than the
enum's. Keyed by the status itself so a status with no entry is a ``KeyError`` at the
render, not a mail that quietly says nothing - and :data:`~agentdag.application.kernel.
notify.NOTIFIABLE_STATUSES` is the set this must cover."""


class MailNotifier:
    """Sends one plain-text mail per run event through the repo's email adapter.

    The ``send_notification`` callable is injected rather than imported, so a test drives
    the sink through the same seam production does instead of standing in for SMTP.
    """

    def __init__(self, *, send_notification: SendNotification, config: EmailConfig) -> None:
        """Bind the delivery function and the mail configuration to send under.

        Args:
            send_notification: The email adapter's notification sender.
            config: SMTP hosts, sender and recipients; the operator's own mailbox.
        """
        self._send_notification = send_notification
        self._config = config

    def emit(self, event: RunEvent) -> None:
        """Send ``event`` as one mail.

        Anything the delivery raises is left to propagate: containing it is
        :func:`~agentdag.application.kernel.notify.emit_best_effort`'s job at the call
        site, and swallowing it here as well would hide the failure from a caller that
        deliberately wants it (a ``notify test`` verb, a sink conformance test).

        Args:
            event: The run event to tell the operator about.
        """
        self._send_notification(
            config=self._config,
            subject=_subject(event),
            message=_message(event),
        )


def _subject(event: RunEvent) -> str:
    """Render the subject line: the run, and what happened to it.

    The run id leads because an operator watching several runs sorts by it, and the
    headline follows so a phone notification that truncates still says which run.

    Args:
        event: The run event to describe.

    Returns:
        A one-line subject.

    Example:
        >>> from agentdag.application.kernel.notify import RunEvent
        >>> _subject(RunEvent(run_id="r1", workflow="graph-a", status=RunStatus.DONE,
        ...                   at="2026-08-21T14:12:03+00:00"))
        'agentdag run r1 finished'
    """
    return f"agentdag run {event.run_id} {_HEADLINE[event.status]}"


def _message(event: RunEvent) -> str:
    """Render the body: the run's identity, then whatever the status adds to it.

    Args:
        event: The run event to describe.

    Returns:
        A plain-text body, one fact per line.

    Example:
        >>> from agentdag.application.kernel.notify import RunEvent
        >>> print(_message(RunEvent(run_id="r1", workflow="graph-a", status=RunStatus.FAILED,
        ...                         at="2026-08-21T14:12:03+00:00")))
        run:      r1
        workflow: graph-a
        status:   failed
        at:       2026-08-21T14:12:03+00:00
    """
    lines = [
        f"run:      {event.run_id}",
        f"workflow: {event.workflow}",
        f"status:   {event.status.value}",
        f"at:       {event.at}",
    ]
    return "\n".join(lines + _decision_lines(event))


def _decision_lines(event: RunEvent) -> list[str]:
    """Render what a person needs in order to ANSWER, or nothing when no answer is wanted.

    Only a suspend carries these, and it carries all three: which node asked, what it
    asked, and how long the asking stands before the default is applied unattended. A
    notification missing the deadline would let somebody read it, plan to answer
    tomorrow, and find the run already decided.

    Args:
        event: The run event to describe.

    Returns:
        The extra body lines, empty for any status other than ``suspended``.
    """
    if event.status is not RunStatus.SUSPENDED:
        return []
    return ["", f"node:      {event.node_id}", f"decide by: {event.decide_by}", "", event.summary]
