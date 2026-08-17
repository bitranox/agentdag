"""PreToolUse hooks for the Claude executor: deny writes outside the isolation root,
deny listed Bash commands (design 7, M2 probe).

Both factories return a pure function of its input dict - no ``claude_agent_sdk``
import here at all - matching ``claude_agent_sdk.types.HookCallback`` read from source
(0.2.139): ``Callable[[HookInput, str | None, HookContext], Awaitable[HookJSONOutput]]``,
an async function taking the raw hook-input dict, the tool_use_id (or ``None``) and a
hook context, returning ``{}`` to allow or a ``hookSpecificOutput`` deny payload. Typing
the input as a plain ``dict[str, Any]`` and the context as ``object | None`` (rather than
the SDK's own TypedDicts) is what keeps both hooks unit-testable with a synthetic dict
and no SDK import (``tests/test_kernel_executor_claude.py``); it also happens to be
what ``HookMatcher.hooks: list[HookCallback]`` accepts, since a function whose declared
parameter types are broader than the SDK's is still assignable there (a narrower
caller can only ever pass what the SDK actually passes).

The M2 probe (``workflow/design/probes/m2-hooks-dontask.md`` in RESEARCH) measured that
the SDK genuinely invokes these under ``permission_mode="dontAsk"`` and respects a
``deny`` - an in-root ``Write`` still succeeds with no prompt, an out-of-root ``Write``
is denied with :func:`deny_outside_root`'s own reason text, and a denylisted ``Bash``
command is denied with :func:`deny_bash_commands`'s. It ALSO measured that neither hook
sees a write made through ``Bash`` shell redirection instead of the matched tool - by
design (``2026-08-17-agentdag-design.md`` section 7): the isolation-root scan (Task 13)
is the backstop for that gap, not these hooks.

Contents:
    * :data:`HookResult` - the JSON-ish dict a hook returns.
    * :func:`deny_outside_root` - factory: denies Write/Edit/MultiEdit/NotebookEdit
      whose target resolves outside a root.
    * :func:`deny_bash_commands` - factory: denies a Bash command matching a denylist
      substring.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

__all__ = ["HookCallback", "HookResult", "deny_bash_commands", "deny_outside_root"]

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


def deny_outside_root(isolation_root: Path) -> HookCallback:
    """Build a ``PreToolUse`` hook denying any write outside ``isolation_root``.

    Matched (by the caller's ``HookMatcher``) against ``Write|Edit|MultiEdit|
    NotebookEdit``. Resolves ``tool_input["file_path"]`` with ``os.path.realpath``
    before comparing - a symlink under the root pointing outside it, or a ``..``
    segment, resolves to its real target either way, so both routes out are caught
    the same way a literal outside path is (M2 probe, run 2: measured to both allow
    an in-root ``Write`` with no prompt and deny an out-of-root one).

    Args:
        isolation_root: The node's isolation root; a target resolving outside this
            (after ``realpath``) is denied.

    Returns:
        The hook callback, closed over ``isolation_root``.
    """
    root_real = os.path.realpath(isolation_root)

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: object) -> HookResult:
        del tool_use_id, context  # unused: the decision depends only on the tool input
        tool_input: dict[str, Any] = input_data.get("tool_input") or {}
        file_path = tool_input.get("file_path")
        if not file_path:
            return {}
        target = Path(os.path.realpath(file_path))
        if target == Path(root_real) or Path(root_real) in target.parents:
            return {}
        return _deny(f"{file_path} resolves to {target}, outside the isolation root {root_real}")

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
