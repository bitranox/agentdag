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
        ("max", "output", "tokens"),
        ("estimated", "tokens"),
        ("estimated", "tokens", "delta"),
        ("ephemeral", "1h", "input", "tokens"),
        ("ephemeral", "5m", "input", "tokens"),
        ("truncated", "by", "token", "cap"),
    }
)
"""The narrow exception to :data:`SECRET_KEY_COMPONENTS`'s default-redact stance:
ten usage-accounting fields the kernel executor streams for every turn - each one an
INTEGER count or a boolean about a count, never a secret - identified by SPLIT
COMPONENTS (not the raw string), so one entry covers every spelling the SDK uses for
that field at once (``cache_read_input_tokens``/``cacheReadInputTokens`` both split to
``("cache", "read", "input", "tokens")``).

This allowlist governs LEAVES, not subtrees. A dict value always gets walked (see
:func:`scrub`'s dict branch) whether its key is allowlisted, secret, or neither - so
an entry here only changes what happens to a key whose value is itself a scalar
(an int or a bool). Listing a CONTAINER-valued key here would not be a no-op the way
it might look: :data:`SECRET_KEY_COMPONENTS` still evaluates that key's own name
independently of what type its value is, so a container key containing a secret word
component (as every field in this set does, being built from ``tokens``) is redacted
WHOLE the moment it is taken off this list, same as any other unenumerated key - the
allowlist is what stands between it and that outcome, not between its children and
theirs. ``output_tokens_details`` was tried here and removed for exactly this reason:
it is a dict in a real transcript, and this codebase's own archived transcripts show
no nested keys under it worth naming individually (nothing here invents field names
from another provider's schema) - so the entry protected only the container's own
shape (a walkable, mostly-empty dict instead of a bare ``"[scrubbed]"`` string), a
distinction with no security consequence given nothing meaningful was found inside
it. A future nested field worth keeping gets its OWN entry when someone can name and
verify it, exactly like every entry already here.

The first four are the per-turn token counts (``input_tokens``, ``output_tokens``,
``cache_creation_input_tokens``, ``cache_read_input_tokens``); redacting them made
every archived transcript's per-turn usage permanently unreadable, exactly the audit
trail a security reviewer needs to reconstruct a dispatch's spend. The other six were
found still redacted after that first fix, by checking all 138 distinct keys the real
transcripts under ``/var/lib/agentdag/runs`` carry against the shipped allowlist:
``max_output_tokens``, ``estimated_tokens``, ``estimated_tokens_delta``,
``truncated_by_token_cap`` (all numeric or boolean usage metadata), and
``ephemeral_1h_input_tokens`` / ``ephemeral_5m_input_tokens`` - the cache-tier
breakdown that is the ONLY evidence in a transcript of which prompt-caching TTL a
turn used, load-bearing for reconstructing a dispatch's actual cache-read cost, not
merely informative.

The exception is enumerated and stays small ON PURPOSE: it grows only when someone
identifies a specific field and adds it deliberately, never by widening the shape
this set matches (e.g. "anything ending in a tokens component"). A token-count-shaped
key NOT on this list - a future SDK field, or a key that only looks like a count
(``access_tokens``, a plural noun ending in the same word, but naming an actual
credential) - is still redacted by default. That is the trade this module makes:
audit-trail lines cost less than a silently under-redacted secret."""


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
