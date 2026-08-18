"""Redact a secret-shaped dict key or a secret-shaped string value (design 9).

Pure: nothing but :mod:`re` (the whole implementation is two regexes and a recursive
walk), so it belongs in the domain layer, not the adapter that first needed it -
:mod:`agentdag.adapters.kernel.executor_claude` redacts every streamed SDK message
before it reaches ``transcript.jsonl`` and a failed dispatch's own result text before
it reaches ``record.json``; :mod:`agentdag.application.kernel.dispatch` redacts a
raising node body's own exception text before IT reaches ``record.json`` too - the
same sink, so a body's exception string (which can carry a header an HTTP client
echoed back) deserves the identical guarantee.

Contents:
    * :data:`SECRET_KEY_RE` - a dict key matching this has its value redacted whole.
    * :data:`SECRET_TOKEN_SHAPE_RE` - a string value matching this, anywhere, is redacted.
    * :func:`scrub` - the recursive redaction.
"""

from __future__ import annotations

import re
from typing import Any, cast

__all__ = ["SECRET_KEY_RE", "SECRET_TOKEN_SHAPE_RE", "scrub"]

SECRET_KEY_RE = re.compile(r"(?i)token|secret|password|authorization|credential")
"""The KEY pass: a dict value is redacted whole when its own key matches this -
catches ``{"password": "hunter2"}`` regardless of what the value looks like."""

SECRET_TOKEN_SHAPE_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}|oat01-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}"
    r"|pypi-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{16,}"
)
"""The VALUE pass: a STRING value - reached under any key, inside a list, or as the
top-level value itself - has every substring matching this (a known secret token
shape) redacted, regardless of its key - catches a token echoed back under an
innocuous key (e.g. ``{"content": "leaked: sk-ant-oat01-..."}``) that
:data:`SECRET_KEY_RE` alone would never see."""


def scrub(value: Any) -> Any:
    """Recursively redact a secret-shaped dict KEY or a secret-shaped string VALUE.

    Two independent passes, applied together on every recursive call: the KEY pass
    (:data:`SECRET_KEY_RE`) replaces a dict value whole when its own key looks like a
    secret; the VALUE pass (:data:`SECRET_TOKEN_SHAPE_RE`) redacts a matching
    substring inside any string, wherever it is reached. Anything else - a string
    that is neither secret-keyed nor secret-shaped, a number, a bool, ``None`` -
    passes through unchanged.

    Args:
        value: A JSON-shaped value - typically a dict rendered from a streamed SDK
            message, or a bare string - about to be written to disk.

    Returns:
        A structurally identical copy with every secret-keyed value and every
        secret-shaped string substring replaced by ``"[scrubbed]"``.

    Example:
        >>> scrub({"tool_input": {"password": "hunter2", "content": "see sk-ant-oat01-ABCDEFGH"}})
        {'tool_input': {'password': '[scrubbed]', 'content': 'see [scrubbed]'}}
    """
    if isinstance(value, dict):
        mapping = cast("dict[Any, Any]", value)
        return {key: "[scrubbed]" if SECRET_KEY_RE.search(str(key)) else scrub(val) for key, val in mapping.items()}
    if isinstance(value, list):
        elements = cast("list[Any]", value)
        return [scrub(item) for item in elements]
    if isinstance(value, str):
        return SECRET_TOKEN_SHAPE_RE.sub("[scrubbed]", value)
    return value
