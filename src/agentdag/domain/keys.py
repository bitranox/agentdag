"""The content-addressed journal key (design 3.2): which fields IDENTIFY a call, stated in code.

Contents:
    * :func:`canonical_json` - stable, sorted, compact JSON of any value.
    * :func:`content_hash` - ``sha256:<hex>`` of a piece of text.
    * :func:`record_hash` - content hash of one :class:`~agentdag.domain.models.ResultRecord`.
    * :func:`prefix_hash` - content hash chaining a node's dependency records in order.
    * :func:`journal_key` - the key a dispatch is served from and journaled under.
    * :func:`hash8` - the short form used in a node's on-disk directory name.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .models import NodeSpec, ResultRecord

__all__ = ["KEY_VERSION", "canonical_json", "content_hash", "hash8", "journal_key", "prefix_hash", "record_hash"]

KEY_VERSION = "v2"
"""The journal key format version (design 3.2); bump it, never the hash algorithm alone,
when the set of identity fields or their encoding changes."""

_IDENTITY_FIELDS = (
    "kind",
    "executor",
    "tier_role",
    "model",
    "effort",
    "knowledge",
    "stage_into",
    "write_set",
    "requires",
    "isolation",
    "attempt",
    "continuation",
    "compact",
)
"""The :class:`~agentdag.domain.models.NodeSpec` fields that IDENTIFY a dispatch (design 3.2).
``deadline_s``, ``budget`` (limits) and ``brief_ref`` (a path, not an input) are deliberately
excluded: changing them does not make it a different call. ``deps`` (the raw node-id list) is
also excluded: a dependency's contribution to identity is its RESULT, already carried by
``prefix`` (each dependency's :func:`record_hash` embeds its own ``node_id``), so including the
raw id list here would double-count it. ``compact`` IS identity: two specs differing only in
``compact.trigger_tokens``/``keep_last_n`` are different calls and must not share a key."""


def canonical_json(value: Any) -> str:
    """Render a value as stable, sorted, compact JSON.

    Args:
        value: Any JSON-serialisable value.

    Returns:
        Compact JSON text with keys sorted at every level.

    Example:
        >>> canonical_json({"b": 1, "a": [2, {"d": None, "c": "x"}]})
        '{"a":[2,{"c":"x","d":null}],"b":1}'
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(text: str) -> str:
    """Hash a piece of text into a content-addressed identifier.

    Args:
        text: The text to hash.

    Returns:
        ``"sha256:<hex digest>"``.

    Example:
        >>> content_hash("brief").startswith("sha256:")
        True
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_hash(record: ResultRecord) -> str:
    """Content-hash one result record, over its canonical JSON.

    Args:
        record: The record to hash.

    Returns:
        ``"sha256:<hex digest>"`` of the record's canonical JSON.

    Example:
        >>> from agentdag.domain.models import NodeStatus, ResultRecord
        >>> a = ResultRecord(node_id="g", attempt=0, status=NodeStatus.DONE, artefact_refs=[],
        ...                  key_facts={}, typed_fields=[], tokens=None, charged_tokens={},
        ...                  cost_usd=None, duration_s=0.1, executor_used="code", model_used="-",
        ...                  effort_used="-", knowledge_used=[], input_hash="sha256:0")
        >>> record_hash(a) == record_hash(a)
        True
    """
    return content_hash(canonical_json(record.model_dump(mode="json", by_alias=True)))


def prefix_hash(dep_records: Sequence[ResultRecord]) -> str:
    """Chain a node's dependency records, in order, into one content hash.

    Args:
        dep_records: The result records of every dependency, in a stable order.

    Returns:
        ``"sha256:<hex digest>"`` chaining each dependency's :func:`record_hash`,
        so a changed or reordered dependency set changes the prefix.
    """
    return content_hash("\0".join(record_hash(r) for r in dep_records))


def journal_key(spec: NodeSpec, *, brief_hash: str, input_hash: str, prefix: str) -> str:
    """Compute the content-addressed journal key a dispatch is served from (design 3.2).

    Only the spec's identity fields (see :data:`_IDENTITY_FIELDS`) participate;
    limits (``deadline_s``, ``budget``) and the brief's own path (``brief_ref``) do
    not, so changing a limit or moving the brief never re-dispatches an unchanged call.

    Args:
        spec: The node spec being dispatched.
        brief_hash: Content hash of the resolved brief text.
        input_hash: Content hash of the assembled input.json.
        prefix: :func:`prefix_hash` of the node's dependency records.

    Returns:
        ``"v2:sha256:<hex digest>"``.
    """
    identity = spec.model_dump(mode="json", include=set(_IDENTITY_FIELDS))
    identity["brief_hash"] = brief_hash
    identity["input_hash"] = input_hash
    digest = hashlib.sha256((prefix + "\0" + canonical_json(identity)).encode("utf-8")).hexdigest()
    return f"{KEY_VERSION}:sha256:{digest}"


def hash8(key: str) -> str:
    """Return the short form of a journal key, used in a node's on-disk directory name.

    Args:
        key: A journal key of the form ``"v2:sha256:<hex digest>"``.

    Returns:
        The first 8 hex characters of the digest.

    Example:
        >>> hash8("v2:sha256:" + "ab" * 32)
        'abababab'
    """
    return key.rsplit(":", 1)[-1][:8]
