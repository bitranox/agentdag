"""Telling the operator what a run did: the ``Notifier`` port and the event it carries (design 3.4).

A run finishes, fails, crashes, or stops to ask a human a question - and the process that
knew is gone by the time anyone looks. This is the one seam that reaches OUT to a person
instead of to disk, so it is deliberately the narrowest port the kernel has: one method,
one typed record, no return value to branch on.

Two properties are load-bearing, and both are about who is allowed to speak:

* **Only the coordinator and the deadline pass emit.** Never a node. A node runs under an
  executor with a write set and a denylist, and giving it a channel to mail the operator
  would put an un-gated side effect inside the sandbox. The mail here is the OPERATOR's
  channel about the run, not the run's own effect on the world, which is why it does not
  go through ``stage``/``apply`` (design 2.4's rule is about a workflow's effects; this is
  not one).
* **A sink cannot fail a run.** :func:`emit_best_effort` swallows whatever a sink raises,
  because a mail server being down is not a run failure and a finished run reported as
  failed for want of a notification is strictly worse than a notification nobody got.

Contents:
    * :data:`NOTIFIABLE_STATUSES` - the four run states worth interrupting a person for.
    * :class:`RunEvent` - what one notification says.
    * :class:`Notifier` - the port every sink implements.
    * :func:`emit_best_effort` - emit without letting a sink's failure reach the run.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Protocol

from ...domain.models import RunStatus

__all__ = ["NOTIFIABLE_STATUSES", "Notifier", "RunEvent", "emit_best_effort"]

NOTIFIABLE_STATUSES = (RunStatus.SUSPENDED, RunStatus.DONE, RunStatus.FAILED, RunStatus.CRASHED)
"""The four states a person is told about, and the reason the other three are absent.

``running`` is not an event, it is the ordinary condition of every live run.
``cancelling`` and ``cancelled`` are the states an operator reaches by ASKING for them,
so they carry no news: whoever ran ``run cancel`` already knows. What is left is exactly
the set a run arrives at without anybody watching - it finished, it broke, it died, or it
is waiting on an answer only a person can give.
"""


@dataclass(frozen=True, slots=True)
class RunEvent:
    """What one notification says: which run reached which state, and what a decider needs.

    Frozen, so a sink cannot edit the event on its way through and hand the next sink a
    different story.

    Attributes:
        run_id: The run's id, and its directory name under ``runs/``.
        workflow: The workflow the run is executing.
        status: The state reached; one of :data:`NOTIFIABLE_STATUSES`.
        at: When it was reached, rendered by
            :func:`~agentdag.application.kernel.ports.format_stamp` from the emitting
            side's own clock reading - never by the sink, which would date the message
            by when the mail was composed rather than by when the run did the thing.
        node_id: The approve node the run is waiting on; ``None`` on every other status.
        summary: The approve payload's own text, so the person deciding sees the
            question in the notification rather than having to open the run first.
            Empty on every other status.
        decide_by: The payload's own deadline, after which the default is applied
            unattended. ``None`` on every other status. Read from the payload and never
            recomputed here, for the same reason
            :mod:`~agentdag.application.kernel.approve` never recomputes it: the
            payload's content hash IS the approve node's identity.
    """

    run_id: str
    workflow: str
    status: RunStatus
    at: str
    node_id: str | None = None
    summary: str = ""
    decide_by: str | None = None

    def __post_init__(self) -> None:
        """Refuse a status nobody should be interrupted for.

        Raises:
            ValueError: ``status`` is not one of :data:`NOTIFIABLE_STATUSES`.
        """
        if self.status not in NOTIFIABLE_STATUSES:
            notifiable = ", ".join(status.value for status in NOTIFIABLE_STATUSES)
            raise ValueError(f"{self.status.value!r} is not one of the notifiable statuses: {notifiable}")


class Notifier(Protocol):
    """Where a run event goes: a mailbox, a client push, or nowhere at all."""

    def emit(self, event: RunEvent) -> None:
        """Deliver ``event``, or do nothing.

        No return value on purpose: a caller cannot usefully act on "the mail was
        accepted", and offering a boolean would invite one to try. An implementation
        that CANNOT deliver may raise - :func:`emit_best_effort` is where that is
        contained - but a sink that can reasonably stay quiet should.
        """
        ...


def emit_best_effort(notifier: Notifier, event: RunEvent) -> None:
    """Emit ``event``, and let nothing a sink raises reach the run.

    The asymmetry is deliberate and it only goes one way: a notification's failure must
    never change a run's outcome, while a run's outcome is exactly what the notification
    is about. A sink whose SMTP host is unreachable, whose credentials expired, or whose
    push client went away has failed at telling somebody - the run itself already
    happened, and rewriting its verdict to ``failed`` because of that would report the
    opposite of the truth.

    The cost, stated rather than hidden: a failing sink is SILENT here. Nothing is
    journaled (the journal has one writer, the coordinator, and a notification is not one
    of its line types) and nothing is printed (this is the application layer). An
    operator whose mail sink is misconfigured therefore sees no notifications and no
    error. Revisit when a second sink ships or when a ``notify_failed`` journal line
    earns its schema change; until then the no-op sink is the default, so the silent path
    is the one almost every run takes anyway.

    Args:
        notifier: The sink to deliver through.
        event: What to say.
    """
    # Every exception, deliberately: a sink is third-party-ish code at the edge of the
    # system, and there is no subset of failures it is safe to let through - see above.
    with contextlib.suppress(Exception):
        notifier.emit(event)
