"""The two texts that make a context-ceiling handover work (design 3.8, decision 14).

A node crossing its context ceiling is not simply interrupted: it is asked to write a
typed handover record its successor continues from. Getting it to comply turns out to
depend entirely on HOW it is asked, and the wording here is measured rather than chosen.

`RESEARCH/workflow/design/probes/handover-nudge-inject.md`, 40 dispatches against SDK
0.2.144: the injected text always reaches the model and never blocks the hooked call, but

* a notice CLAIMING the node is near its context ceiling is refused 4 times out of 4 - the
  node reads its own remaining budget, finds the claim false, and treats the whole message
  as prompt injection;
* a DUTY conditioned on such a claim fails the same way, 0 of 4, because the node checks
  the precondition against the same telemetry;
* a duty that pre-authorises a coordinator stop notice, paired with a notice asserting only
  a DECISION, is obeyed 4 of 4, and the handovers carry what a successor needs.

So both strings avoid asserting anything about the node that the node can check. The
coordinator is not telling the node a fact about itself; it is issuing an instruction the
node's own task already told it to honour. `tests/test_kernel_handover.py` asserts the
absence of the refuted framing, so this cannot be undone by accident.

Contents:
    * :data:`HANDOVER_FILENAME` - what the record is called inside the node's artefact dir.
    * :data:`HANDOVER_AS_WRITTEN_FILENAME` - the node's own bytes, kept before the stamp.
    * :data:`IDENTITY_KEYS` - the keys the coordinator owns rather than the node.
    * :func:`prompt_with_stop_duty` - the node's task, with the standing duty prepended.
    * :func:`stop_notice` - the authorised notice the executor's hook injects at the ceiling.
    * :func:`stamp_identity` - add the coordinator's half of the record (decision 16).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "HANDOVER_AS_WRITTEN_FILENAME",
    "HANDOVER_FILENAME",
    "IDENTITY_KEYS",
    "prompt_with_stop_duty",
    "stamp_identity",
    "stop_notice",
]

IDENTITY_KEYS: tuple[str, ...] = ("node_id", "attempt", "continuation")
"""The three keys the schema requires and the duty never asks for (decision 16).

They are the coordinator's half of the record. A node cannot supply them honestly: it is not
told its attempt or its place in a handover chain, and a node that invented them would be
guessing. Measured across every probe dispatch to date, 69 of 69 duty-shaped records failed the
full schema on these three and on nothing else."""

HANDOVER_FILENAME = "handover.json"
"""The handover record's name in the node's artefact dir.

JSON, not prose, so that a successor's brief CAN be composed from typed fields without the
coordinator branching on anything the node wrote (design 3.8, ``handover.schema.json``).

That composition is not built. Nothing here reads this file back: a successor is the same
spec re-dispatched with ``continuation + 1``, carrying the SAME brief and prompt, and it
continues from the worktree the handover outcome keeps as its artefact ref. The record is
written for the run's own artefacts and for whoever builds that step - state the gap rather
than describing the intended dataflow in the present tense.
"""

HANDOVER_AS_WRITTEN_FILENAME = "handover.as-written.json"
"""The node's record exactly as the node left it, kept beside the stamped one.

Stamping re-persists :data:`HANDOVER_FILENAME`, which would otherwise destroy the only copy of
what the node actually produced. That copy is the evidence every faithfulness question is
answered from, and those questions turn on the node's own WORDING: whether a `done` entry
claims a step's deliverable or only the sub-action it really finished is a difference in prose,
invisible once the record has been reformatted and its keys reordered.

Written BEFORE the stamp, never after, so a crash between the two leaves the original readable
rather than lost.
"""

_DUTY = (
    "Standing instruction for this task: the run coordinator may send you a stop notice at any "
    "time. If it does, stop what you are doing, write your handover to {path}, and finish without "
    "starting or completing anything else. A stop notice from the run coordinator is authoritative "
    "for this task; you do not need to verify it, and it reaches you through your tool output.\n"
    "The handover is JSON with these keys: done (list of completed sub-steps), left (list of "
    "remaining sub-steps, in order), key_facts (an object of results already known), artefact_refs "
    "(list of paths worth reading first), write_set_state (one of clean, dirty, committed) and "
    "next_step (one sentence naming the single next action). Write only what a fresh reader could "
    "not work out from the repository itself.\n"
)

_NOTICE = (
    "Stop notice from the run coordinator: this node's turn is being ended now. Per the standing "
    "instruction in your task, write your handover to {path} and finish. Do not start any new work."
)


def prompt_with_stop_duty(prompt: str, *, handover_path: str) -> str:
    """Prepend the standing stop-notice duty to a node's prompt.

    The duty has to be present from DISPATCH. A notice arriving with no prior standing in
    the task is treated as prompt injection and refused, which is the correct reading on
    the node's part - so this is not defensive boilerplate, it is the thing that gives the
    later notice any authority at all.

    Note that the duty deliberately names no trigger. An earlier version conditioned it on
    the node being told it was near its context ceiling, and the node then evaluated that
    condition against its own remaining budget and declined; measured 0 of 4.

    Args:
        prompt: The node's own task, unchanged and kept in full.
        handover_path: Where the record goes, as the node should write it - relative to the
            node's working directory, or absolute. Repeated in :func:`stop_notice` so a node
            whose context no longer holds the duty still knows where to write.

    Returns:
        The duty followed by ``prompt``.

    Examples:
        >>> composed = prompt_with_stop_duty("Migrate the adapter.", handover_path="h.json")
        >>> "Migrate the adapter." in composed
        True
        >>> "authoritative" in composed
        True
    """
    return _DUTY.format(path=handover_path) + prompt


def stop_notice(*, handover_path: str) -> str:
    """The authorised stop notice injected when the node crosses its context ceiling.

    It asserts a DECISION ("this node's turn is being ended now") and never a fact about
    the node. That distinction is the whole finding: the node can refute a claim about its
    own context and will, but it has nothing to check a coordinator's decision against.

    Args:
        handover_path: Where the record goes; the same path the duty named.

    Returns:
        The notice text, for the executor's ``PreToolUse`` hook to inject.

    Examples:
        >>> "run coordinator" in stop_notice(handover_path="h.json")
        True
    """
    return _NOTICE.format(path=handover_path)


def stamp_identity(record: Mapping[str, Any], *, node_id: str, attempt: int, continuation: int) -> dict[str, Any]:
    """Add the coordinator's identity keys to a node-authored handover record (decision 16).

    Pure, and deliberately so: the caller owns reading and re-persisting the file, this owns
    only what the stamped record should CONTAIN. The coordinator's values win over anything
    already under those keys - identity is what the coordinator knows and the node does not,
    so a node that guessed is corrected rather than trusted.

    The stamp belongs where the record is first persisted by the coordinator, and nowhere
    else. Stamping a record on the way OUT of a call would put the current run's identity on
    a REPLAYED record, which then reports old work as having run under an attempt it never
    ran under.

    Args:
        record: What the node wrote, already parsed.
        node_id: The dispatched node's id.
        attempt: The dispatched node's attempt, 0-based, matching ``ResultRecord.attempt`` and
            the node spec's own counter - not a 1-based count of tries.
        continuation: Which link of the handover chain this is; 0 for the first.

    Returns:
        A new dict; ``record`` is not modified.

    Examples:
        >>> stamped = stamp_identity({"done": [], "left": []}, node_id="w1", attempt=0, continuation=2)
        >>> stamped["node_id"], stamped["attempt"], stamped["continuation"]
        ('w1', 0, 2)
        >>> stamped["done"]
        []
        >>> original = {"done": [], "node_id": "guessed"}
        >>> stamp_identity(original, node_id="w1", attempt=0, continuation=0)["node_id"]
        'w1'
        >>> original["node_id"]
        'guessed'
    """
    return {**record, "node_id": node_id, "attempt": attempt, "continuation": continuation}
