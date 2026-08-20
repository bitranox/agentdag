"""Redact a secret-shaped dict key or a secret-shaped string value (design 9).

Pure: nothing but :mod:`re` (component splitting plus one shape regex, behind a
recursive walk), so it belongs in the domain layer, not the adapter that first needed
it - :mod:`agentdag.adapters.kernel.executor_claude` redacts every streamed SDK
message before it reaches ``transcript.jsonl`` and a failed dispatch's own result text
before it reaches ``record.json``; :mod:`agentdag.application.kernel.dispatch`
redacts a raising node body's own exception text before IT reaches ``record.json``
too - the same sink, so a body's exception string (which can carry a header an HTTP
client echoed back) deserves the identical guarantee. Those streamed SDK messages are
JSON objects with a mix of snake_case and camelCase keys.

Contents:
    * :data:`SECRET_KEY_COMPONENTS` - a dict key with a component matching one of
      these words has its value redacted whole.
    * :data:`USAGE_COUNT_KEY_ALLOWLIST` - the narrow, enumerated exception.
    * :data:`SECRET_TOKEN_SHAPE_RE` - a string value matching this, anywhere, is redacted.
    * :func:`scrub` - the recursive redaction.
"""

from __future__ import annotations

import re
from typing import Any, cast

__all__ = ["SECRET_KEY_COMPONENTS", "SECRET_TOKEN_SHAPE_RE", "USAGE_COUNT_KEY_ALLOWLIST", "scrub"]

_ACRONYM_TO_WORD_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_LOWER_TO_UPPER_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")


def _key_components(key: str) -> tuple[str, ...]:
    """Split a dict key into its lowercase identifier components.

    A real key in this codebase's JSON messages is snake_case (``api_token``) OR
    camelCase (``apiToken``, ``cacheReadInputTokens``) - the streamed SDK payloads the
    kernel executor scrubs are full of the latter (``inputTokens``, ``apiKeySource``,
    ``costUSD``), so a splitter that only understood underscores would leave every
    camelCase secret key unmatched. Two passes, in order so an acronym run survives
    intact: first mark an acronym-to-word boundary (``APIToken`` -> ``API_Token``, so
    the run ``API`` is not split into ``A_P_I``), then mark every lower/digit-to-upper
    boundary (``cacheReadInputTokens`` -> ``cache_Read_Input_Tokens``). Finally split
    on every non-alphanumeric separator (``_``, ``-``, ``.``, ...) and lowercase each
    piece so comparison is case-insensitive.
    """
    marked = _ACRONYM_TO_WORD_BOUNDARY_RE.sub("_", key)
    marked = _LOWER_TO_UPPER_BOUNDARY_RE.sub("_", marked)
    return tuple(component.lower() for component in _NON_ALNUM_RE.split(marked) if component)


SECRET_KEY_COMPONENTS = frozenset(
    {
        "token",
        "tokens",
        "secret",
        "secrets",
        "password",
        "passwords",
        "authorization",
        "authorizations",
        "credential",
        "credentials",
    }
)
"""A dict value is redacted whole when any COMPONENT of its own key (see
:func:`_key_components`) is one of these words, singular or plural - catches
``{"password": "hunter2"}``, ``{"auth_token": "..."}`` and ``{"apiToken": "..."}``
regardless of what the value looks like, and regardless of whether the key is
snake_case or camelCase.

This is a default-REDACT predicate, deliberately: an unanticipated key that happens
to contain one of these words gets its value hidden, which costs at most a line in
the audit trail. The alternative - default-KEEP with a list of known-bad keys - lets
an unanticipated secret-shaped key (a new field a future SDK version adds, a header
name nobody thought to list) reach disk in the clear, which is the actual security
regression this module fixes. See :data:`USAGE_COUNT_KEY_ALLOWLIST` for the narrow,
enumerated exception this default-redact stance requires."""

USAGE_COUNT_KEY_ALLOWLIST = frozenset(
    {
        ("input", "tokens"),
        ("output", "tokens"),
        ("cache", "creation", "input", "tokens"),
        ("cache", "read", "input", "tokens"),
    }
)
"""The narrow exception to :data:`SECRET_KEY_COMPONENTS`'s default-redact stance: the
four usage-accounting fields the kernel executor streams for every turn - each one an
INTEGER count of tokens, not a secret - in both spellings the SDK uses
(``input_tokens``/``inputTokens``, ``output_tokens``/``outputTokens``,
``cache_creation_input_tokens``/``cacheCreationInputTokens``,
``cache_read_input_tokens``/``cacheReadInputTokens``). A key's SPLIT COMPONENTS are
compared against this set (not the raw string), so one entry here covers both
spellings at once. Redacting these made every archived transcript's per-turn usage
permanently unreadable, exactly the audit trail a security reviewer needs to
reconstruct a dispatch's spend - but the exception is enumerated and small on
purpose: a token-count field NOT on this list (``maxOutputTokens``,
``estimated_tokens``, a future SDK field) is still redacted by default, trading an
audit-trail line for never silently under-redacting an unanticipated key."""


def _is_secret_key(key: str) -> bool:
    """True when ``key`` should have its value redacted whole (the KEY pass)."""
    components = _key_components(key)
    if components in USAGE_COUNT_KEY_ALLOWLIST:
        return False
    return any(component in SECRET_KEY_COMPONENTS for component in components)


SECRET_TOKEN_SHAPE_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}|oat01-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}"
    r"|pypi-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{16,}"
)
"""The VALUE pass: a STRING value - reached under any key, inside a list, or as the
top-level value itself - has every substring matching this (a known secret token
shape) redacted, regardless of its key - catches a token echoed back under an
innocuous key (e.g. ``{"content": "leaked: sk-ant-oat01-..."}``) that
:data:`SECRET_KEY_COMPONENTS` alone would never see."""


def scrub(value: Any) -> Any:
    """Recursively redact a secret-shaped dict KEY or a secret-shaped string VALUE.

    Two independent passes, applied together on every recursive call: the KEY pass
    (:data:`SECRET_KEY_COMPONENTS`, via :func:`_is_secret_key`) replaces a dict value
    whole when its own key looks like a secret; the VALUE pass
    (:data:`SECRET_TOKEN_SHAPE_RE`) redacts a matching
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
        return {key: "[scrubbed]" if _is_secret_key(str(key)) else scrub(val) for key, val in mapping.items()}
    if isinstance(value, list):
        elements = cast("list[Any]", value)
        return [scrub(item) for item in elements]
    if isinstance(value, str):
        return SECRET_TOKEN_SHAPE_RE.sub("[scrubbed]", value)
    return value
