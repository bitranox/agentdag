"""The approve node's default: the rule that makes one valid, and applying it at its deadline (design 3.4).

Design 3.4 says the deadline HAS AN OWNER: "the server, or a systemd timer unit when no
server runs, applies the payload's default at ``decide_by`` ... the exited coordinator
cannot, and leaving it to whichever client happens to be watching is not an authority."
This module is that owner's mechanism. Three candidates were weighed before building it,
and the reasoning is recorded here because the choice is the load-bearing part:

* A CHECK INSIDE ``run resume``/``run start`` needs no new mechanism, and is not an owner:
  a default would apply only when somebody runs a command, so a run whose decider went
  away - the ONE case ``decide_by`` exists for - would wait forever. It also fires on the
  runs least in need of it, since a run somebody is resuming is a run somebody is
  attending. Rejected, and deliberately NOT added as a second, implicit trigger: two
  triggers with different semantics is two policies to reason about.
* A COORDINATOR-SIDE CHECK at the suspend point cannot exist: the coordinator EXITS at a
  suspend, which is the whole reason the deadline needs an owner at all.
* A PERIODIC EXTERNAL PASS (``agentdag run apply-deadlines``, driven by the user timer
  under ``deploy/``) is the only one that is an owner. Task 21 showed what putting code
  outside a run's lock costs, so this takes the lock the way
  :func:`~agentdag.application.kernel.cancel.resolve_cancel` does - and the rest of that
  cost does not transfer: applying a default is not a kill, it is the SAME write-once,
  temp-then-link decision write a human's ``run approve`` performs
  (:meth:`~agentdag.application.kernel.ports.RunDir.write_decision`), so a human and this
  pass racing for one (node id, payload hash) resolve on the filesystem's own atomic link:
  exactly one wins and the loser is REFUSED, never overwritten and never silently dropped.

Taking the run's lock has one honest cost, stated rather than hidden: a coordinator
launch that lands in the microseconds this pass holds the lock fails with ``LockHeld``
and is re-run by its caller. The alternative - applying an unattended default to a run a
coordinator has just taken the lock for - is worse, because "unattended" is exactly what
the lock is evidence about.

What this module must NOT do, and the reason is an identity: ``decide_by`` is read from
the payload's own field and never recomputed here. The payload's content hash IS the
approve node's dispatch identity (design 3.4's binding), so a deadline read from the
clock would move on every launch, change the hash, and re-dispatch an approve node the
journal already holds - see ``application.workflows.graph_a._decide_by``, which derives
it from the run's own ``run_started`` line for that same reason. The clock is read here
for exactly one question: has that fixed instant passed?

Contents:
    * :data:`SYSTEM_IDENTITY` - the ``by`` a decision the system applied carries.
    * :data:`TIMER_TOKEN_ID` - the ``token_id`` naming this pass as the applying agent.
    * :data:`DEADLINE_REASON` - the ``reason`` such a decision carries.
    * :func:`suspend_payload_rel` - where a suspend published the payload being decided.
    * :func:`validate_approve_payload` - design 2.4's approve rules: a default with no
      external effect, and operator-facing text a person can actually be shown.
    * :class:`DeadlineOutcome` - what one pass over ONE run reports back.
    * :func:`apply_due_default` - apply one run's default if its deadline has passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ...domain.kernel_errors import LockHeld, RunRefused, SpecRejected
from ...domain.keys import content_hash, hash8
from ...domain.models import ApprovePayload, Decision, RunStatus

if TYPE_CHECKING:
    from ...domain.models import LockHolder
    from .ports import Clock, RunDir, RunLock

__all__ = [
    "DEADLINE_REASON",
    "SYSTEM_IDENTITY",
    "TIMER_TOKEN_ID",
    "DeadlineOutcome",
    "apply_due_default",
    "suspend_payload_rel",
    "validate_approve_payload",
]

SYSTEM_IDENTITY = "system"
"""The reserved ``by`` a decision the SYSTEM applied carries, not a person's account name.

``journal-line.schema.json``'s ``approve_decision.by`` names this value itself ("'system'
when the deadline owner applied the payload's default"), and it is what
``application.kernel.summary`` reads to tell an unattended default from a human answer.
It is an IDENTITY, not an authentication: nothing stops an OS account literally called
``system`` from writing a decision under it, exactly as nothing stops any process of the
same account from writing one at all (the task that would change that is L1's token file,
deliberately out of scope here)."""

TIMER_TOKEN_ID = "agentdag-approve-timer"  # nosec B105  # noqa: S105 - a token IDENTITY, not a secret
"""The ``token_id`` a system-applied default carries: the AGENT that applied it.

Named for the shipped unit under ``deploy/`` rather than for a user, so a journal reader
can tell a default applied by this periodic pass from one a future server applied, while
:data:`SYSTEM_IDENTITY` stays the single field that answers "was a human involved"."""

DEADLINE_REASON = "deadline"
"""The ``reason`` a system-applied default carries (design 3.4: ``{by: system, reason: deadline}``)."""

MAX_OPERATOR_TEXT_CHARS = 4096
"""How much operator-facing text a payload may carry before a person stops reading it.

Four times the largest text the shipping workflow can produce: graph A's push list is one
line per repo of the form ``  <repo>  <12-char sha>`` (about 46 characters at the longest
repo name in the fleet it was built for, roughly 20 repos), so about 1000 characters. The
headroom is for a workflow with more to say; a plan that needs more than this is asking a
person to approve something they will not have read, which is not an approval.

A constant rather than a policy knob on purpose: it bounds what a HUMAN reads, which is
not a property of one run. Promote it to ``thresholds`` when a workflow genuinely needs a
different figure, not before."""

MAX_BODY_LINE_OCTETS = 998
"""The longest line a mail body may carry (RFC 5322 2.1.1), which this text becomes verbatim.

``adapters.kernel.notify_mail`` puts the payload's text into the body unchanged, so a
longer line is a line the standard says a server may refuse or mangle - and one nobody
reads either way."""

_FIRST_PRINTABLE = 0x20
"""Space: everything below it is a C0 control, tab and carriage return included."""

_DEL = 0x7F
"""DEL, immediately followed by the C1 control block that ends at :data:`_LAST_C1`."""

_LAST_C1 = 0x9F
"""The last C1 control; the printable Latin-1 supplement starts one code point above."""

_UNRENDERED = frozenset(
    "\u200b\u200c\u200d\u200e\u200f\u2028\u2029\u202a\u202b\u202c\u202d\u202e"
    "\u2060\u2061\u2062\u2063\u2064\u2066\u2067\u2068\u2069\ufeff"
)
"""Characters that are stored but not shown, or that reorder what is shown around them.

Zero-width and word-joiner characters, the line/paragraph separators, and the bidirectional
overrides and isolates. They are the Trojan Source shape: the reader approves the rendering
and the decision is bound to the bytes."""

_LOCK_HELD_REASON = "the run lock is held (a live coordinator, or a concurrent deadline pass)"


class _NotAppliedError(Exception):
    """Internal: why one pass applies no default to one run; its text becomes the reported reason.

    Private and never raised out of this module: :func:`apply_due_default` turns it into a
    :class:`DeadlineOutcome`. A run the pass cannot act on is not an error - a runs
    directory is full of runs no default is due for - so the caller gets a reportable
    reason rather than an exception to sort through.
    """


class _NotSuspendedError(_NotAppliedError):
    """Internal: the run is not waiting on a decision at all.

    A separate type rather than a distinguishable message, because
    :attr:`DeadlineOutcome.awaiting_decision` is exactly this distinction and nothing that
    reports on a pass should have to recognise it by parsing prose.
    """


def validate_approve_payload(payload: ApprovePayload) -> None:
    """Refuse a payload design 2.4 will not let a human be asked to decide.

    Two rules, one entry point, two callers, deliberately in one place: the coordinator
    checks when the approve node RUNS (so a workflow offering an unanswerable payload
    fails before anybody is asked anything), and :func:`apply_due_default` checks again at
    the moment it would apply a default unattended - which is where 2.4's reason for
    existing cashes out ("a default is what the deadline owner applies unattended, so it
    may never be the option that pushes"). A second entry point would let one caller grow
    a rule the other does not have.

    The text rules REFUSE rather than normalise, and that is forced rather than chosen:
    the payload's content hash IS the approve node's dispatch identity (design 3.4's
    binding), so rewriting the text would re-key the node and detach a decision already
    recorded against the old hash. The author fixes the text; nothing here edits it.

    Args:
        payload: The approve payload on offer.

    Raises:
        SpecRejected: ``payload.default`` names no option in ``payload.options`` or names
            one whose effect is ``"external"``; or the operator-facing text is empty,
            longer than a person is asked to read, carries a line no mail body may hold,
            or carries characters that make what renders differ from what is hashed. Every
            reason is reported at once, because 2.4's remedy is ONE automatic re-ask
            carrying them and a second refusal suspends.
    """
    reasons = _default_reasons(payload) + _text_reasons(payload.text)
    if reasons:
        raise SpecRejected("; ".join(reasons))


def _default_reasons(payload: ApprovePayload) -> list[str]:
    """Return 2.4's default rule: a default must name an option that leaves the process alone.

    Args:
        payload: The approve payload on offer.

    Returns:
        One reason, or none when the default names a no-effect option.
    """
    options_by_id = {option.id: option for option in payload.options}
    default_option = options_by_id.get(payload.default)
    if default_option is None or default_option.effect != "none":
        return [f"approve default {payload.default!r} does not name a no-effect option"]
    return []


def _text_reasons(text: str) -> list[str]:
    """Return every reason the operator-facing text may not be put in front of a person.

    Design 2.4 governs what a planner emits, and decision 8 (2026-08-22) made ``approve``
    plan-expressible: this text is model-authored from here on, and it reaches the operator
    verbatim as a notification body (``application.kernel.notify.RunEvent.summary``). It
    had no definition, bound or normalisation of any kind before that decision, which is
    the obligation the decision carries.

    Args:
        text: The payload's operator-facing text.

    Returns:
        Every reason to refuse it, empty when a person may be shown it.
    """
    return _size_reasons(text) + _character_reasons(text)


def _size_reasons(text: str) -> list[str]:
    """Return the reasons the text is the wrong SIZE to be read or to be mailed.

    Args:
        text: The payload's operator-facing text.

    Returns:
        Every size reason, empty when it fits.
    """
    reasons: list[str] = []
    if not text.strip():
        reasons.append("the operator text is empty, so the payload asks nothing")
    if len(text) > MAX_OPERATOR_TEXT_CHARS:
        reasons.append(
            f"the operator text is {len(text)} characters, over the {MAX_OPERATOR_TEXT_CHARS} "
            "a person is asked to read before deciding"
        )
    over = [n for n, line in enumerate(text.split("\n"), start=1) if len(line.encode()) > MAX_BODY_LINE_OCTETS]
    if over:
        reasons.append(
            f"line {over[0]} of the operator text is over {MAX_BODY_LINE_OCTETS} octets, "
            "which is more than a mail body line may carry (RFC 5322 2.1.1)"
        )
    return reasons


def _character_reasons(text: str) -> list[str]:
    """Return the reasons the text renders as something other than what it is.

    A decision is bound to the payload's HASH, so any character that makes the rendered
    text differ from the stored text detaches the approval from what the person approved:
    an escape sequence rewrites a terminal line, and a bidi override reorders one while
    the bytes stay put. Both are refused rather than stripped.

    Args:
        text: The payload's operator-facing text.

    Returns:
        Every character reason, empty when the text renders as itself.
    """
    reasons: list[str] = []
    controls = sorted({ch for ch in text if ch != "\n" and _is_control(ch)})
    if controls:
        reasons.append("the operator text carries a control character: " + _code_points(controls))
    hidden = sorted(set(text) & _UNRENDERED)
    if hidden:
        reasons.append(
            "the operator text carries an invisible or direction-changing character: " + _code_points(hidden)
        )
    return reasons


def _is_control(ch: str) -> bool:
    """Return whether ``ch`` is a C0 or C1 control (DEL included), which no operator text needs.

    A tab counts: its width is up to whoever renders it, so a list aligned with tabs is a
    list the reader and the author see differently.

    Args:
        ch: One character.

    Returns:
        Whether it is a control character.
    """
    code = ord(ch)
    return code < _FIRST_PRINTABLE or _DEL <= code <= _LAST_C1


def _code_points(chars: list[str]) -> str:
    """Render offending characters as code points, since printing them back is the problem.

    Args:
        chars: The characters to name.

    Returns:
        A comma-separated list of ``U+XXXX`` code points.
    """
    return ", ".join(f"U+{ord(ch):04X}" for ch in chars)


@dataclass(frozen=True, slots=True)
class DeadlineOutcome:
    """What one pass over ONE run reports back.

    Attributes:
        run_id: The run this pass looked at.
        node_id: The approve node whose default was applied; ``""`` whenever
            :attr:`applied` is ``False``. A refusal names the node inside :attr:`reason`
            instead, because a pass that never got as far as reading the state file has
            no node id to give and a half-filled field would read like one it did.
        applied: Whether this pass WROTE a decision file. The caller's list of runs to
            relaunch is built from this, never from a fresh state query.
        decision: The option id applied; ``""`` when nothing was.
        reason: Why nothing was applied; ``""`` when something was.
        awaiting_decision: Whether the run was - or could not be confirmed NOT to be -
            waiting on a decision when this pass looked. ``False`` only for a run
            confirmed not suspended, which is the ordinary state of most runs in a runs
            directory. A periodic caller reports on the rest and stays quiet about these,
            without any caller having to parse :attr:`reason`'s prose.
    """

    run_id: str
    node_id: str
    applied: bool
    decision: str
    reason: str
    awaiting_decision: bool


def suspend_payload_rel(node_id: str, payload_hash: str) -> str:
    """Return the run-relative path of the payload a suspend published for ``node_id``.

    One function rather than the same f-string in four places: the coordinator writes
    this path, this module reads it to apply a default, the CLI reads it to show a human
    the question, and the notification sink reads it to put that question in the mail.
    A convention four callers spell out by hand is a convention that drifts.

    Note this is the SUSPEND payload's location - named by the payload's CONTENT hash -
    not the copy an approve node's own dispatch writes under its journal-key hash. The
    two agree only by coincidence; see
    :meth:`~agentdag.application.kernel.context.Coordinator.approve`.

    Args:
        node_id: The approve node that suspended.
        payload_hash: The content hash of the payload it is waiting on.

    Returns:
        The path relative to the run directory's root.

    Example:
        >>> suspend_payload_rel("a_push_list", "sha256:abcdef0123456789")
        'nodes/a_push_list/abcdef01/payload.json'
    """
    return f"nodes/{node_id}/{hash8(payload_hash)}/payload.json"


def apply_due_default(run_dir: RunDir, *, lock: RunLock, clock: Clock, holder: LockHolder) -> DeadlineOutcome:
    """Apply ``run_dir``'s approve default if its ``decide_by`` has passed, under the run's lock.

    Writes at most one decision file and nothing else: no journal line (folding one is the
    coordinator's job on the next relaunch, which is where the run's single journal writer
    lives) and no state change. So this is safe to run on every run in a runs directory,
    repeatedly, at any time - a run with nothing due is left exactly as it was.

    Args:
        run_dir: The run to look at; ``run_dir.root.name`` is its id.
        lock: The run's lock port; held for the whole check, released before returning.
        clock: The one seam this reads wall-clock time through - and it is read for ONE
            question only, whether the payload's own ``decide_by`` has passed. The
            deadline itself is never recomputed here (see the module docstring).
        holder: This process's identity, recorded as the lock's holder while held.

    Returns:
        What this pass did, or why it did nothing.
    """
    run_id = run_dir.root.name
    try:
        token = lock.acquire(run_dir.root, holder)
    except LockHeld:
        return _not_applied(run_id, node_id="", reason=_LOCK_HELD_REASON, awaiting_decision=True)
    try:
        return _apply_under_lock(run_dir, clock=clock)
    except _NotSuspendedError as ordinary:
        return _not_applied(run_id, node_id="", reason=str(ordinary), awaiting_decision=False)
    except _NotAppliedError as skipped:
        return _not_applied(run_id, node_id="", reason=str(skipped), awaiting_decision=True)
    finally:
        lock.release(token)


def _apply_under_lock(run_dir: RunDir, *, clock: Clock) -> DeadlineOutcome:
    """Do the whole check with the run's lock held; every refusal raises :class:`_NotAppliedError`.

    Raises:
        _NotAppliedError: nothing is applied, and the message says why.
    """
    node_id, payload_hash = _waiting_on(run_dir)
    payload = _payload_on_offer(run_dir, node_id=node_id, payload_hash=payload_hash)
    _refuse_before_decide_by(payload, clock=clock, node_id=node_id)
    _refuse_an_unapplyable_default(payload, node_id=node_id)
    return _write_default(run_dir, node_id=node_id, payload_hash=payload_hash, payload=payload)


def _not_applied(run_id: str, *, node_id: str, reason: str, awaiting_decision: bool) -> DeadlineOutcome:
    """Shape a "nothing applied" outcome; the one place that shape is built."""
    return DeadlineOutcome(
        run_id=run_id,
        node_id=node_id,
        applied=False,
        decision="",
        reason=reason,
        awaiting_decision=awaiting_decision,
    )


def _waiting_on(run_dir: RunDir) -> tuple[str, str]:
    """Return the (node id, payload hash) this run is suspended on.

    Raises:
        _NotAppliedError: the state file is unreadable or missing, the run is not
            suspended at all, or it is suspended but names no payload to answer.
    """
    try:
        state = run_dir.read_state()
    except (RunRefused, OSError) as unreadable:
        raise _NotAppliedError(f"state.json is unreadable: {unreadable}") from unreadable
    if state.status is not RunStatus.SUSPENDED:
        raise _NotSuspendedError(f"not suspended (status={state.status.value})")
    if state.cursor is None or state.cursor_payload_hash is None:
        raise _NotAppliedError("suspended, but state.json names no node and payload hash to answer")
    return state.cursor, state.cursor_payload_hash


def _payload_on_offer(run_dir: RunDir, *, node_id: str, payload_hash: str) -> ApprovePayload:
    """Read the payload the run is suspended on, and prove it is the one ``state.json`` names.

    The content check is what makes everything after it trustworthy: a payload whose hash
    matches is byte-for-byte the one the coordinator itself wrote and already validated,
    so a hand-edited payload cannot talk this pass into applying a default nobody was
    offered.

    Raises:
        _NotAppliedError: the payload file is missing or unreadable, or its content does
            not hash to ``payload_hash``.
    """
    rel = suspend_payload_rel(node_id, payload_hash)
    try:
        text = run_dir.read_text(rel)
    except (OSError, ValueError) as unreadable:
        raise _NotAppliedError(f"{node_id}: the payload at {rel} is unreadable: {unreadable}") from unreadable
    if content_hash(text) != payload_hash:
        raise _NotAppliedError(f"{node_id}: the payload at {rel} does not match state.json's cursor_payload_hash")
    try:
        return ApprovePayload.model_validate_json(text)
    except ValueError as unreadable:  # defensive only: the hash above already proves the bytes
        raise _NotAppliedError(f"{node_id}: the payload at {rel} does not parse: {unreadable}") from unreadable


def _refuse_before_decide_by(payload: ApprovePayload, *, clock: Clock, node_id: str) -> None:
    """Refuse until the payload's OWN ``decide_by`` has passed.

    ``payload.decide_by`` is read, never recomputed: it is part of the payload whose
    content hash IS this approve node's dispatch identity, so a deadline derived from the
    clock here would differ from the one the decider was shown and, worse, invite the same
    mistake on the writing side, where it would re-dispatch an approve the journal already
    holds. The model pins the field to an explicit ``+00:00`` offset, so
    :meth:`datetime.fromisoformat` yields an aware UTC instant comparable with the clock's.

    Raises:
        _NotAppliedError: the deadline has not passed yet.
    """
    due = datetime.fromisoformat(payload.decide_by)
    if clock.now() < due:
        raise _NotAppliedError(f"{node_id}: not due until {payload.decide_by}")


def _refuse_an_unapplyable_default(payload: ApprovePayload, *, node_id: str) -> None:
    """Apply design 2.4's approve rules at the moment of unattended application.

    The default half is the one this pass exists to re-check. The text half is all but
    unreachable here and is kept for the same reason it is one entry point: a payload only
    reaches disk through a suspend that already validated it, and ``_payload_on_offer``
    re-checks its content hash, so the text can differ from a validated one only for a
    payload written by an older version of this code.

    Raises:
        _NotAppliedError: the payload fails a 2.4 approve rule - reported rather than
            raised out, so one bad payload cannot stop a sweep from serving the other runs.
    """
    try:
        validate_approve_payload(payload)
    except SpecRejected as refused:
        raise _NotAppliedError(f"{node_id}: {refused}") from refused


def _write_default(run_dir: RunDir, *, node_id: str, payload_hash: str, payload: ApprovePayload) -> DeadlineOutcome:
    """Write the default as a decision, write-once, and report it applied.

    No "is there already a decision" pre-check: the write itself is the check.
    :meth:`~agentdag.application.kernel.ports.RunDir.write_decision` publishes with an
    atomic hard link that raises ``FileExistsError`` when the target exists, so a human
    answering the same (node id, payload hash) at the same moment cannot be overwritten -
    whoever loses the link loses, and is told so. A pre-check would only add a window
    between looking and writing that the link already closes.

    Raises:
        _NotAppliedError: this (node id, payload hash) already has a decision.
    """
    decision = Decision(
        node_id=node_id,
        decision=payload.default,
        reason=DEADLINE_REASON,
        by=SYSTEM_IDENTITY,
        token_id=TIMER_TOKEN_ID,
        payload_hash=payload_hash,
    )
    try:
        run_dir.write_decision(decision)
    except FileExistsError as raced:
        raise _NotAppliedError(f"{node_id}: this payload already has a decision") from raced
    return DeadlineOutcome(
        run_id=run_dir.root.name,
        node_id=node_id,
        applied=True,
        decision=payload.default,
        reason="",
        awaiting_decision=True,
    )
