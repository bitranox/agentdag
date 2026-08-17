"""The isolation-root manifest and its diff: pure comparison, no I/O (design C8).

A ``scan`` node judges a code node's writes against its declared write set by
comparing two manifests - one taken before the node ran, one after. Everything
here is pure: taking the manifest itself is :class:`~agentdag.adapters.kernel.isolation_scan.IsolationScanner`'s
job (an adapter, since it reads the filesystem); this module only compares two
already-taken manifests and judges the difference against a set of allowed globs.

Contents:
    * :data:`Manifest` - relative POSIX path -> content hash.
    * :func:`diff_manifests` - every path added, content-changed or removed between two manifests.
    * :func:`stray_paths` - which of those paths no allowed glob covers.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["Manifest", "diff_manifests", "stray_paths"]

Manifest = dict[str, str]
"""Relative POSIX path -> ``sha256:<hex>`` of the file's content (design C8)."""


def diff_manifests(before: Manifest, after: Manifest) -> list[str]:
    """Return every path added, content-changed, or removed between two manifests, sorted.

    Judged on CONTENT hash only, never a mode bit: a path present in both
    manifests with the identical hash is not a finding, whatever else changed
    about the file on disk (:class:`~agentdag.adapters.kernel.isolation_scan.IsolationScanner`
    never reads a mode bit into the manifest in the first place).

    Args:
        before: The manifest taken before the node ran.
        after: The manifest taken after the node ran.

    Returns:
        Every relative path whose hash differs between the two manifests - added
        (only in ``after``), changed (in both, different hash), or removed (only
        in ``before``) - sorted for a deterministic finding list. A deletion is
        included here and left for :func:`stray_paths` to judge by path: deleting
        something inside the write set is fine, deleting something outside it
        is a finding exactly like writing there.

    Example:
        >>> diff_manifests({"a": "sha256:1", "b": "sha256:2"}, {"a": "sha256:1", "c": "sha256:3"})
        ['b', 'c']
    """
    changed = {path for path, digest in after.items() if before.get(path) != digest}
    removed = set(before) - set(after)
    return sorted(changed | removed)


def stray_paths(changed: Sequence[str], *, allowed: Sequence[str]) -> list[str]:
    """Return every ``changed`` path that no ``allowed`` glob covers, in the order given.

    Args:
        changed: The diff to judge - typically :func:`diff_manifests`'s output.
        allowed: The write-set globs (plus any node-dir and run-root exception
            prefixes a caller adds) a node may legitimately touch. A pattern
            ending in ``/**`` covers the directory itself and everything under it.
            A plain ``*`` already spans ``/`` too - :mod:`fnmatch` gives ``*`` no
            special meaning for the path separator - so ``wt/a/*`` alone grants
            the WHOLE subtree under ``wt/a/``, not just its immediate children,
            the same as ``wt/a/**`` would.

    Returns:
        Every path in ``changed`` matched by NONE of ``allowed`` - the isolation
        finding a ``scan`` node fails on.

    Example:
        >>> stray_paths(["wt/a/f.py", "wt/b/g.py"], allowed=["wt/a/**"])
        ['wt/b/g.py']
    """
    return [path for path in changed if not any(_matches(path, pattern) for pattern in allowed)]


def _matches(path: str, pattern: str) -> bool:
    """Return whether ``path`` falls under one write-set glob, ``**`` included.

    :mod:`fnmatch` has no special meaning for ``/`` - its ``*`` already spans
    path separators - so translating a trailing ``**`` down to a single ``*``
    is enough to make ``dir/**`` match anything under ``dir/``. What plain
    translation misses is the prefix directory itself with no suffix at all
    (``dir`` with no trailing ``/...``), which ``dir/*`` never matches since the
    literal ``/`` in the pattern has nothing to pair with - so that case is
    checked separately, by exact prefix.

    Args:
        path: A relative POSIX path from a manifest diff.
        pattern: One glob from the caller's allowed list.

    Returns:
        Whether ``path`` is covered by ``pattern``.
    """
    if fnmatch.fnmatchcase(path, pattern.replace("**", "*")):
        return True
    if pattern.endswith("/**"):
        return path == pattern[: -len("/**")]
    return False
