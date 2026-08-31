"""Guard the tracked tree against local-infrastructure detail reaching the public repo.

`PLANS/` and `handover.md` became tracked on 2026-09-01 after a hand read-through removed
build hostnames, absolute paths into the developer's tree, a borrowed venv, a private sibling
project and an SSH key filename. Future sessions edit those documents and will paste the same
shapes back in, and forgetting is silent: the consequence is permanent public git history. So
the read-through is replaced here by something that cannot be forgotten.

Two kinds of rule, for two different reasons:

* The STRUCTURAL rules live here, in the public repo, because a shape is not a disclosure. They
  also catch a mount path or key filename nobody has seen yet, which a list of known-bad names
  never can.
* The private-NAME rules cannot live here: a blocklist of hostnames publishes the hostnames it
  exists to hide. They are read from a gitignored file when one is present, so they bind the
  local `make test` that gates every push, and are reported as absent rather than silently
  skipped in CI, where a contributor has no such names to leak anyway.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# This module states the patterns, so it necessarily matches them. It is the ONE file the scan
# cannot cover, and the assertion below pins that exclusion to exactly this file.
SELF = Path(__file__).resolve()

GIT_EXECUTABLE = shutil.which("git") or "git"
"""Absolute path of the git CLI, falling back to the bare name so a missing git still fails
with a plain ``FileNotFoundError`` naming the command."""


def tracked_names() -> list[str]:
    """Return the repo-relative name of every tracked file.

    Returns:
        One entry per tracked path.

    Notes:
        ``-z`` is required: ``git ls-files`` quotes a non-ASCII path by default, so joining the
        quoted literal onto the root yields a file that does not exist and the scan skips it.
    """
    # Suppression below: a resolved executable and a fixed argument list, never a shell string.
    out = subprocess.run(  # nosec B603  # noqa: S603
        [GIT_EXECUTABLE, "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    return [name for name in out.split("\0") if name]


PRIVATE_MARKERS_FILE = REPO_ROOT / ".private-markers"

# Absolute paths under roots that a real machine mounts and documentation does not use as an
# example. `/home/` and `/Users/` are deliberately NOT here: `/home/op/...` in a config comment
# is a placeholder, and firing on it would train the reader to ignore this test.
NON_FHS_MOUNT_PATH = re.compile(r"(?:^|[^A-Za-z0-9_<`.-])/(?:media|mnt|srv|rotek)/[A-Za-z0-9_.-]+")

# A key or certificate named after the account it authenticates, e.g. user@host_nopass.key.
# The filename alone is an infrastructure detail even though it carries no key material.
ACCOUNT_BEARING_KEY_FILE = re.compile(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+\.(?:key|pem)\b")

STRUCTURAL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("an absolute path under a real mount root", NON_FHS_MOUNT_PATH),
    ("a key file named after an account", ACCOUNT_BEARING_KEY_FILE),
)


def tracked_files() -> list[Path]:
    """Return every tracked file as an absolute path, excluding this module.

    Returns:
        The tracked paths the scan covers.
    """
    paths = [REPO_ROOT / name for name in tracked_names()]
    return [p for p in paths if p.resolve() != SELF]


def readable_text(path: Path) -> str | None:
    """Return a file's text, or None when it is binary or unreadable.

    Args:
        path: The file to read.

    Returns:
        The decoded text, or None when the file cannot contribute to the scan.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def findings(pattern: re.Pattern[str]) -> list[str]:
    """Return every tracked line matching a pattern, as `path:line: text`.

    Args:
        pattern: The pattern to search for.

    Returns:
        One entry per matching line, ready to print in an assertion message.
    """
    hits: list[str] = []
    for path in tracked_files():
        text = readable_text(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()[:120]}")
    return hits


@pytest.mark.parametrize(("description", "pattern"), STRUCTURAL_RULES, ids=[r[0] for r in STRUCTURAL_RULES])
def test_no_tracked_file_carries(description: str, pattern: re.Pattern[str]) -> None:
    """No tracked file may carry a shape that only a real machine produces."""
    hits = findings(pattern)
    assert not hits, f"tracked files carry {description}:\n" + "\n".join(hits)


def test_the_scan_excludes_exactly_this_module() -> None:
    """The one file the scan cannot cover is this one, and no other.

    Without this, a later edit widening the exclusion would silently unguard whole directories
    while every other test in the module still passed.
    """
    all_tracked = {(REPO_ROOT / name).resolve() for name in tracked_names()}
    scanned = {p.resolve() for p in tracked_files()}
    assert all_tracked - scanned <= {SELF}


def test_no_tracked_file_carries_a_private_name() -> None:
    """No tracked file may carry a name from the local private-marker list.

    The list is gitignored, so this binds the local gate that runs before every push. It cannot
    be shipped: writing the hostnames into a public test would publish exactly what the list
    exists to keep out.
    """
    if not PRIVATE_MARKERS_FILE.exists():
        pytest.skip(f"no {PRIVATE_MARKERS_FILE.name} present, so there are no private names to check for here")
    names = [
        line.strip()
        for line in PRIVATE_MARKERS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert names, f"{PRIVATE_MARKERS_FILE.name} exists but lists no names, so this test would pass vacuously"
    pattern = re.compile("|".join(re.escape(n) for n in names))
    hits = findings(pattern)
    assert not hits, "tracked files carry a private name:\n" + "\n".join(hits)
