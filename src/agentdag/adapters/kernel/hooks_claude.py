"""PreToolUse hooks for the Claude executor: deny writes outside the isolation root,
deny listed Bash commands (design 7, M2 probe).

Both factories return a pure function of its input dict - no ``claude_agent_sdk``
import here at all - matching ``claude_agent_sdk.types.HookCallback`` read from source
(0.2.139): ``Callable[[HookInput, str | None, HookContext], Awaitable[HookJSONOutput]]``,
an async function taking the raw hook-input dict, the tool_use_id (or ``None``) and a
hook context, returning ``{}`` to allow or a ``hookSpecificOutput`` deny payload. Typing
the input as a plain ``dict[str, Any]`` and the context as ``object | None`` (rather than
the SDK's own TypedDicts) is what keeps both hooks unit-testable with a synthetic dict
and no SDK import (``tests/test_kernel_executor_claude.py`` calls them with a minimal
literal dict and ``None`` for ``tool_use_id``/``context`` - neither satisfies the SDK's
own ``HookInput``/``HookContext`` TypedDicts, which carry several other required fields
no test needs to fabricate; verified directly against pyright, not assumed - a hook
typed against the SDK's own ``HookInput``/``HookContext`` rejects that exact call with
``reportArgumentType``).

This does NOT structurally satisfy ``HookMatcher.hooks: list[HookCallback]`` on its own
- also verified directly against pyright: assigning a hook typed this way to the SDK's
``HookCallback`` fails on TWO independent grounds. (1) Parameter contravariance: the
assignment needs the SDK's ``HookInput`` assignable to this module's ``dict[str, Any]``,
but pyright treats a TypedDict as NOT assignable to ``dict[str, Any]`` (invariant,
mutable - inserting an arbitrary key through the wider view would violate the
TypedDict's closed key set). (2) Return-type covariance: ``Awaitable``'s type parameter
IS covariant, so the assignment needs this module's return type (``dict[str, Any]``) to
be a SUBTYPE of the SDK's ``HookJSONOutput`` - but ``HookJSONOutput`` is the NARROWER
type (a union of specific TypedDicts), so it is ``dict[str, Any]`` that would need to
narrow, not the other way round; covariance fails for the mirror-image reason parameter
(1) does. :func:`~.executor_claude._as_sdk_hooks` bridges this with a documented,
targeted ``cast`` at the one call site that hands these hooks to ``HookMatcher`` - see
its docstring for the M2 probe's runtime proof that the mismatch is a type-system
limitation, not a real behavioural gap.

The M2 probe (``workflow/design/probes/m2-hooks-dontask.md`` in RESEARCH) measured that
the SDK genuinely invokes these under ``permission_mode="dontAsk"`` and respects a
``deny`` - an in-root ``Write`` still succeeds with no prompt, an out-of-root ``Write``
is denied with :func:`deny_outside_write_set`'s own out-of-root reason text (the probe
predates the write-set half of that hook), and a denylisted ``Bash``
command is denied with :func:`deny_bash_commands`'s. It ALSO measured that neither hook
sees a write made through ``Bash`` shell redirection instead of the matched tool - by
design (``2026-08-17-agentdag-design.md`` section 7): the isolation-root scan (Task 13)
is the backstop for that gap, not these hooks.

Contents:
    * :data:`HookResult` - the JSON-ish dict a hook returns.
    * :func:`deny_outside_write_set` - factory: denies Write/Edit/MultiEdit/NotebookEdit
      whose target resolves outside a root, or inside it but outside the node's write set.
    * :func:`deny_bash_commands` - factory: denies a Bash command matching a denylist
      substring.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from ...domain.handover import stop_notice
from ...domain.scan import is_covered

__all__ = ["HookCallback", "HookResult", "deny_bash_commands", "deny_outside_write_set", "inject_stop_notice"]

HookResult = dict[str, Any]
"""What a hook returns: ``{}`` to allow, or a ``hookSpecificOutput`` deny payload."""

HookCallback = Callable[[dict[str, Any], "str | None", object], Awaitable[HookResult]]
"""A ``PreToolUse`` hook: matches ``claude_agent_sdk.types.HookCallback`` structurally."""


def _deny(reason: str) -> HookResult:
    """Build a ``PreToolUse`` deny payload carrying ``reason``."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def deny_outside_write_set(isolation_root: Path, *, allowed: Sequence[str]) -> HookCallback:
    """Build a ``PreToolUse`` hook denying any write outside ``isolation_root``.

    Matched (by the caller's ``HookMatcher``) against ``Write|Edit|MultiEdit|
    NotebookEdit``. The path lives under ``tool_input["file_path"]`` for
    ``Write``/``Edit``/``MultiEdit``, but under ``tool_input["notebook_path"]`` for
    ``NotebookEdit`` - both are checked. Resolves it with ``os.path.realpath``
    before comparing - a symlink under the root pointing outside it, or a ``..``
    segment, resolves to its real target either way, so both routes out are caught
    the same way a literal outside path is (M2 probe, run 2: measured to both allow
    an in-root ``Write`` with no prompt and deny an out-of-root one).

    A matched tool call that carries NEITHER key is DENIED, not allowed: this hook
    is the only thing standing between a matched tool and an unrestricted write, so
    a shape it cannot classify must fail closed rather than pass through silently.

    Containment in the root is only the OUTER bound. Inside it, the target's root-relative
    path must also be covered by ``allowed`` - the node's own declared write set plus
    whatever its caller grants it (:func:`~.executor_claude.allowed_writes`). Without that
    second test the run's write containment says only "somewhere in this run", so a node
    could edit a sibling's worktree, another node's artefacts or the run's own
    bookkeeping, and the post-node scan would report it after the fact at best - it cannot
    attribute a write under ``parallel > 1`` at all.

    An EMPTY ``allowed`` denies every write, which is the reading design 2.1 states
    (``write_set`` is "PATHS the node may create, edit or delete"): a node that declared
    nothing may write nothing. It is not "unrestricted within the root", which is what
    this hook meant before the write set reached it.

    Args:
        isolation_root: The node's isolation root; a target resolving outside this
            (after ``realpath``) is denied.
        allowed: The globs, relative to ``isolation_root``, this node may write to.
            Matched by :func:`~agentdag.domain.scan.is_covered`, the same matcher the
            isolation scan judges strays with.

    Returns:
        The hook callback, closed over ``isolation_root`` and ``allowed``.
    """
    root_real = os.path.realpath(isolation_root)
    permitted = tuple(allowed)

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: object) -> HookResult:
        del tool_use_id, context  # unused: the decision depends only on the tool input
        tool_input: dict[str, Any] = input_data.get("tool_input") or {}
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not file_path:
            return _deny("no path in tool input; refusing")
        target = Path(os.path.realpath(file_path))
        if Path(root_real) not in target.parents:
            return _deny(f"{file_path} resolves to {target}, outside the isolation root {root_real}")
        rel = target.relative_to(Path(root_real)).as_posix()
        if not is_covered(rel, permitted):
            return _deny(f"{file_path} resolves to {rel}, which this node's write set does not cover")
        return {}

    return hook


def deny_bash_commands(patterns: tuple[str, ...]) -> HookCallback:
    """Build a ``PreToolUse`` hook denying any Bash command matching a listed pattern.

    Matched (by the caller's ``HookMatcher``) against ``Bash``. ``tool_input["command"]``
    is whitespace-collapsed (``" ".join(command.split())``) before the substring test,
    so extra spaces between words (``"git   push"``) do not evade a pattern written with
    single spaces (M2 probe, run 1 and 2: measured to deny ``git push`` either way).

    Args:
        patterns: Denylisted command substrings, e.g. ``("git push", "gh pr")``.

    Returns:
        The hook callback, closed over ``patterns``.
    """

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: object) -> HookResult:
        del tool_use_id, context
        tool_input: dict[str, Any] = input_data.get("tool_input") or {}
        command = " ".join(str(tool_input.get("command", "")).split())
        for pattern in patterns:
            if pattern in command:
                return _deny(f"command matches denylist pattern {pattern!r}")
        return {}

    return hook


def inject_stop_notice(is_stopping: Callable[[], bool], *, handover_path: str) -> HookCallback:
    """Build a ``PreToolUse`` hook that asks the node to hand over, without blocking it.

    The third hook shape in this module, and the only one that is not a guard: it does not
    decide anything, it puts text in front of the model. Once ``is_stopping()`` returns
    true, every matched tool call carries the authorised stop notice
    (:func:`~agentdag.domain.handover.stop_notice`) as ``additionalContext``.

    It deliberately sends NO ``permissionDecision``. Measured over 40 dispatches
    (RESEARCH ``workflow/design/probes/handover-nudge-inject.md``, decision 14): an
    inject-only return reaches the model in 19 of 20 injecting repeats and the hooked call
    still runs in 40 of 40, so the node stays able to act - which is the point, since what
    it is being asked to do is WRITE its handover. Returning a deny here would stop the node
    before it could produce the record the successor needs.

    ``is_stopping`` is a predicate rather than a flag so it is read at CALL time. The
    executor arms it part-way through a dispatch, and a hook that captured the value when
    it was built would be armed either never or always - the same defect as a body closure
    capturing its spec before the dispatch loop increments it.

    Args:
        is_stopping: Called on every matched tool use; true once the node has crossed its
            context ceiling and should hand over.
        handover_path: Absolute path the node writes its handover record to, repeated in
            the notice so a node whose context no longer holds the duty still knows it.

    Returns:
        The hook callback, closed over the predicate and the path.
    """

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: object) -> HookResult:
        del input_data, tool_use_id, context  # the notice does not depend on which tool ran
        if not is_stopping():
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": stop_notice(handover_path=handover_path),
            }
        }

    return hook
