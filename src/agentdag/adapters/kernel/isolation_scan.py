"""IsolationScanner: a content manifest of a run's isolation root (design C8).

The scanner is the ONLY thing in the isolation-root check that touches the
filesystem - :mod:`agentdag.domain.scan` compares two manifests this adapter
produced, but never reads a file itself.

Contents:
    * :class:`IsolationScanner` - the :class:`~agentdag.application.kernel.ports.IsolationScanner`
      port implementation: walks a run root and hashes every file worth watching.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...domain.scan import Manifest

__all__ = ["IsolationScanner"]

_CHUNK_SIZE = 1024 * 1024
"""Read a watched file in 1 MiB chunks rather than loading it whole (design C8)."""

_CONTROL_FILES = frozenset({"journal.jsonl", "audit.jsonl", "state.json", "lock", "launch.log"})
"""The run's own control files, directly under the run root (design 3.1) - never a
node's write to judge. A node writing a SAME-NAMED file deeper in the tree is not
this exclusion's concern; it is checked by name only at the root level.

``launch.log`` is the coordinator's own bookkeeping too: :meth:`~agentdag.application.kernel.ports.Scope.start`
redirects the background launcher's stdout/stderr there, and it keeps growing for as
long as the run is in progress (the executor's own startup lines land in it), so an
in-progress scan would otherwise see it as a stray write on every single node."""


class IsolationScanner:
    """Content-manifest of a run root: every file hashed, ``.git/`` and the run's own control files skipped."""

    def snapshot(self, root: Path) -> Manifest:
        """Walk ``root`` and hash every file worth watching.

        Args:
            root: The run root to walk.

        Returns:
            Relative POSIX path -> ``sha256:<hex>`` of the file's content, for
            every file under ``root`` except: anything under a ``.git`` directory
            (skipped entirely - git's own object churn is not a stray write), and
            the run's own control files (:data:`_CONTROL_FILES`) directly under
            ``root``.
        """
        manifest: Manifest = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name != ".git"]
            current = Path(dirpath)
            for filename in filenames:
                file_path = current / filename
                rel = file_path.relative_to(root)
                if rel.parts == (filename,) and filename in _CONTROL_FILES:
                    continue
                manifest[rel.as_posix()] = _hash_file(file_path)
        return manifest


def _hash_file(path: Path) -> str:
    """Content-hash ``path`` in :data:`_CHUNK_SIZE` chunks, without reading it whole into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
