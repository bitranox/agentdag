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
    * :func:`prompt_with_stop_duty` - the node's task, with the standing duty prepended.
    * :func:`stop_notice` - the authorised notice the executor's hook injects at the ceiling.
"""

from __future__ import annotations

__all__ = ["HANDOVER_FILENAME", "prompt_with_stop_duty", "stop_notice"]

HANDOVER_FILENAME = "handover.json"
"""The handover record's name in the node's artefact dir.

JSON, not prose: the successor's brief is built from typed fields and the coordinator
branches on none of the text (design 3.8, ``handover.schema.json``).
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
