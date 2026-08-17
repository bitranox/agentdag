"""Graph A (fleet migration) records and pure functions.

Graph A is the baseline coordinator graph: for each repository of a fleet, do the
work, run the gate, tally the outcome, and stage a push intent for every repository
whose gate passed. This module holds only the records those nodes exchange and the
pure functions that decide from them. There is no I/O here - every filesystem,
git and model call lives behind a port in :mod:`agentdag.application.graph_a_ports`.

Contents:
    * :class:`WorkResult` - what one work node reported.
    * :class:`Tally` - one repository's outcome.
    * :class:`TallySummary` - the reduced tally over the fleet.
    * :class:`PushIntent` - one staged push, carrying its dedup key.
    * :func:`parse_repos_text` - read a repository list from text.
    * :func:`dedup_key` - the stable key identifying one push.
    * :func:`reduce_tally` - count passed and failed rows.
    * :func:`stage` - turn passed rows into push intents.
    * :func:`is_scratch_target` - guard: a push target must be a scratch clone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

__all__ = [
    "PushIntent",
    "Tally",
    "TallySummary",
    "WorkResult",
    "dedup_key",
    "is_scratch_target",
    "parse_repos_text",
    "reduce_tally",
    "stage",
]

TallyStatus = Literal["passed", "failed", "work-failed"]
"""Outcome of one repository: gate passed, gate failed, or the work node failed."""


class WorkResult(BaseModel):
    """What one work node reported: typed, never prose."""

    ok: bool
    num_turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    error: str | None = None


class Tally(BaseModel):
    """One repository's outcome after the work node and the gate have run."""

    repo: Path
    status: TallyStatus
    head_sha: str
    test_rc: int | None
    work: WorkResult | None = None


class TallySummary(BaseModel):
    """The reduced tally over the whole fleet."""

    passed: int
    failed: int
    rows: list[Tally]


class PushIntent(BaseModel):
    """One staged push: which repository, which commit, under which dedup key."""

    repo: Path
    head_sha: str
    dedup_key: str


def parse_repos_text(text: str) -> list[Path]:
    """Read a repository list from text: one path per line.

    Blank lines and lines whose first non-space character is ``#`` are ignored,
    and surrounding whitespace is stripped from every remaining line.

    Args:
        text: The contents of a repository list file.

    Returns:
        One :class:`~pathlib.Path` per meaningful line, in file order.

    Example:
        >>> [p.name for p in parse_repos_text("/a/one\\n\\n# c\\n  /b/two  \\n")]
        ['one', 'two']
    """
    return [Path(line.strip()) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def dedup_key(repo: Path, head_sha: str) -> str:
    """Return the stable key identifying one push of one commit to one repository.

    Args:
        repo: The push target.
        head_sha: The commit being pushed.

    Returns:
        ``"<repo name>-<head sha>"``, stable across runs so a replay recognises
        work that is already done.

    Example:
        >>> dedup_key(Path("/x/repo.git"), "f" * 40)
        'repo.git-ffffffffffffffffffffffffffffffffffffffff'
    """
    return f"{repo.name}-{head_sha}"


def reduce_tally(rows: list[Tally]) -> TallySummary:
    """Count how many repositories passed and how many did not.

    Args:
        rows: One row per repository, in any order.

    Returns:
        The summary, carrying the rows unchanged.

    Example:
        >>> summary = reduce_tally([Tally(repo=Path("/o/a.git"), status="passed", head_sha="a", test_rc=0)])
        >>> (summary.passed, summary.failed)
        (1, 0)
    """
    passed = sum(1 for row in rows if row.status == "passed")
    return TallySummary(passed=passed, failed=len(rows) - passed, rows=rows)


def stage(summary: TallySummary) -> list[PushIntent]:
    """Turn every passed row into a push intent.

    Nothing is pushed here: staging only names what a later apply step may do,
    so the list can be shown to a human before anything leaves the process.

    Args:
        summary: The reduced tally.

    Returns:
        One intent per passed row, keyed by repository name and head sha.

    Example:
        >>> summary = reduce_tally([Tally(repo=Path("/o/a.git"), status="failed", head_sha="a", test_rc=1)])
        >>> stage(summary)
        []
    """
    return [
        PushIntent(repo=row.repo, head_sha=row.head_sha, dedup_key=dedup_key(row.repo, row.head_sha))
        for row in summary.rows
        if row.status == "passed"
    ]


def is_scratch_target(repo: Path, scratch_root: Path) -> bool:
    """Report whether a push target is a bare clone under ``<scratch_root>/origin``.

    The baseline never writes to a real repository, so this is the guard the apply
    step consults before every push. Both sides are resolved first, which closes
    the symlink and ``..`` routes out of the scratch tree.

    Args:
        repo: The proposed push target.
        scratch_root: The scratch directory the run owns.

    Returns:
        ``True`` only when ``repo`` lies under ``<scratch_root>/origin``.

    Example:
        >>> is_scratch_target(Path("/scratch/origin/r.git"), Path("/scratch"))
        True
        >>> is_scratch_target(Path("/real/repo"), Path("/scratch"))
        False
    """
    return repo.resolve().is_relative_to((scratch_root / "origin").resolve())
