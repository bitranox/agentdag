# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Derive a SlopCodeBench catalog whose ``checkpoint_N.md`` carries parts 1..N, not part N alone.

The harness renders a checkpoint's prompt from ``<problem>/checkpoint_N.md`` and nothing else, and
in the shipped catalog that file is the DELTA: what part N adds. An agent dispatched on it is
therefore asked for part N while being scored on parts 1..N, including the earlier parts'
regression tests, and it never sees the spec it is being held to. This derives a catalog where
that one file is the cumulative spec, leaving every other byte alone so the same run scores the
same way.

Deriving rather than editing in place is what keeps that claim checkable: the source catalog stays
pristine, and ``CUMULATIVE-MANIFEST.json`` records the source root, the commit it was read at and a
digest per rewritten file, so a result can be traced back to the exact bytes an agent was shown.

Three things here are deliberate, because the obvious version of each fails silently rather than
loudly:

* **One canary survives per file.** Every checkpoint of every problem opens with the same
  byte-identical canary comment, so concatenating the parts yields one canary per part, buried mid
  file. Each part contributes its BODY, and only the newest part's canary is kept, at the top.
* **Parts are ordered numerically.** ``sorted()`` on the file names puts ``checkpoint_10`` between
  1 and 2, which reorders the spec instead of breaking it - there is no error to notice.
* **Everything is validated before anything is written.** A refusal that arrives after the first
  problem has been copied leaves a half-derived destination wearing a manifest that describes all
  of it, which is worse than no output at all.

The rendered file is::

    <canary of checkpoint_N.md, verbatim>
    # Specification, parts 1 to N

    ## Part 1 (already implemented in the workspace)

    <body of checkpoint_1.md>

    ...

    ## Part N (NEW in this checkpoint)

    <body of checkpoint_N.md>

A problem holding no ``checkpoint_<n>.md`` at all is refused rather than copied through: copying
it would be a silent no-op that the manifest records as zero files, which is found out late.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ["MANIFEST_NAME", "CatalogError", "build_catalog", "cumulative_text", "main", "split_canary"]

MANIFEST_NAME = "CUMULATIVE-MANIFEST.json"

_SUMMARY = "Derive a SlopCodeBench catalog whose checkpoint_N.md carries parts 1..N rather than part N alone."

_NEW_MARKER = "(NEW in this checkpoint)"
_PRIOR_MARKER = "(already implemented in the workspace)"

# Names that resolve to something other than a child of the directory they are joined onto.
_UNSAFE_NAMES = frozenset({"", ".", ".."})

# Non-greedy and DOTALL: the canary spans several lines and ends at the FIRST closing delimiter,
# so a body that contains its own HTML comment is not swallowed into the canary.
_CANARY = re.compile(r"\A\s*(<!--.*?-->)", re.DOTALL)
_CHECKPOINT = re.compile(r"checkpoint_(\d+)\.md")


class CatalogError(RuntimeError):
    """The source catalog cannot be read as asked, so no destination is written at all."""


@dataclass(frozen=True)
class _Problem:
    """One problem's validated inputs: where it is, and its checkpoints in numeric order."""

    name: str
    directory: Path
    checkpoints: tuple[Path, ...]


def split_canary(text: str) -> tuple[str, str]:
    """Split a checkpoint file into its leading canary comment and the body after it.

    Args:
        text: One checkpoint file's contents.

    Returns:
        The canary comment verbatim, and everything following it. A file that does not open with
        one yields an empty canary and the whole text as the body, so nothing is ever truncated on
        the strength of a pattern that did not match.
    """
    match = _CANARY.match(text)
    if match is None:
        return "", text
    return match.group(1), text[match.end() :]


def _strip_blank_lines(text: str) -> str:
    """Drop leading and trailing whitespace-only LINES.

    Line-wise rather than ``str.strip()`` so that indentation on the first surviving line - the
    opening of a fenced block, say - is left as the author wrote it.
    """
    lines = text.splitlines()
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


def cumulative_text(bodies: Sequence[str], *, canary: str) -> str:
    """Render parts 1..N as one file, the newest part last and the only one marked NEW.

    Args:
        bodies: Each part's body with its canary already removed, oldest first.
        canary: The newest part's canary, the only one that survives into the output.

    Returns:
        The whole file, ending in exactly one newline.

    Raises:
        CatalogError: ``bodies`` is empty, which would render a specification with no parts in it.
    """
    if not bodies:
        raise CatalogError("a cumulative checkpoint needs at least one part")
    newest = len(bodies)
    blocks = [f"# Specification, parts 1 to {newest}"]
    for number, body in enumerate(bodies, start=1):
        marker = _NEW_MARKER if number == newest else _PRIOR_MARKER
        blocks.append(f"\n## Part {number} {marker}\n\n{_strip_blank_lines(body)}")
    head = f"{canary}\n" if canary else ""
    return head + "\n".join(blocks) + "\n"


def _checkpoint_number(name: str) -> int | None:
    """The part number of a checkpoint file name, or ``None`` when it is not one."""
    match = _CHECKPOINT.fullmatch(name)
    return int(match.group(1)) if match is not None else None


def _checkpoints_of(problem: Path) -> tuple[tuple[int, Path], ...]:
    """Every ``checkpoint_<n>.md`` with its number, ordered by NUMBER rather than by name."""
    numbered = [(_checkpoint_number(path.name), path) for path in problem.iterdir() if path.is_file()]
    return tuple(sorted((number, path) for number, path in numbered if number is not None))


def _unique(names: Iterable[str]) -> tuple[str, ...]:
    """The names in caller order with repeats dropped, so a repeated ``--problem`` builds once."""
    return tuple(dict.fromkeys(names))


def _is_one_component(name: str) -> bool:
    """Whether ``name`` is a single plain path component.

    A problem name is joined onto the destination root and that directory is then REMOVED and
    rewritten, so a name carrying a separator or a traversal element would delete a tree outside
    the destination entirely. Containment is checked here, on the name itself, rather than left to
    follow from some other refusal that happens to reject the same input today.
    """
    return name not in _UNSAFE_NAMES and "/" not in name and "\\" not in name


def _validate(source: Path, names: Sequence[str]) -> tuple[_Problem, ...]:
    """Resolve every named problem before a single byte is written.

    Args:
        source: Root of the shipped catalog.
        names: The problems to derive, already de-duplicated.

    Returns:
        One validated record per name, in the order given.

    Raises:
        CatalogError: The source root is not a directory, a name is not a single path component, a
            named problem is not under the root, or one holds no checkpoints or a gap in them.
    """
    if not source.is_dir():
        raise CatalogError(f"source root {source} is not a directory")
    validated: list[_Problem] = []
    for name in names:
        if not _is_one_component(name):
            raise CatalogError(f"problem name {name!r} is not a single path component, so it could escape the output")
        directory = source / name
        if not directory.is_dir():
            raise CatalogError(f"problem {name!r} is not in {source}")
        numbered = _checkpoints_of(directory)
        if not numbered:
            raise CatalogError(f"problem {name!r} holds no checkpoint_<n>.md files")
        numbers = [number for number, _ in numbered]
        # Part labels are positional, so a gap renames every part after it without any error.
        if numbers != list(range(1, len(numbers) + 1)):
            raise CatalogError(f"problem {name!r} has checkpoints {numbers}, not 1..{len(numbers)}")
        checkpoints = tuple(path for _, path in numbered)
        validated.append(_Problem(name=name, directory=directory, checkpoints=checkpoints))
    return tuple(validated)


def _derive_problem(problem: _Problem, destination: Path) -> dict[str, str]:
    """Copy one problem through and rewrite its checkpoints, returning a digest per rewritten file.

    The directory is copied whole and the checkpoint files overwritten afterwards, so anything the
    catalog gains later - a data file, a deeper solution tree - is carried across without this
    script having to know it exists. An existing destination is removed first: merging would leave
    a stale checkpoint from a longer problem standing beside the new ones.
    """
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(problem.directory, destination)
    bodies: list[str] = []
    digests: dict[str, str] = {}
    for checkpoint in problem.checkpoints:
        canary, body = split_canary(checkpoint.read_text(encoding="utf-8"))
        bodies.append(body)
        # Written as bytes, so the output is LF on every platform and the digest covers exactly
        # the bytes on disk rather than a re-encoding of them.
        payload = cumulative_text(bodies, canary=canary).encode("utf-8")
        (destination / checkpoint.name).write_bytes(payload)
        digests[f"{problem.name}/{checkpoint.name}"] = hashlib.sha256(payload).hexdigest()
    return digests


def build_catalog(source: Path, dest: Path, *, problems: Sequence[str], source_commit: str) -> Path:
    """Derive ``problems`` from ``source`` into ``dest`` and write the manifest.

    Args:
        source: Root of the shipped catalog: a flat directory of problem directories.
        dest: Root of the derived catalog. Named problem directories are replaced, not merged.
        problems: The problem names to derive. Order is kept; repeats are ignored.
        source_commit: The commit ``source`` was read at, recorded in the manifest verbatim.

    Returns:
        The path of the written manifest.

    Raises:
        CatalogError: Anything named on the command line is missing or empty. Nothing is written
            when this is raised.
    """
    names = _unique(problems)
    validated = _validate(source, names)
    dest.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for problem in validated:
        digests.update(_derive_problem(problem, dest / problem.name))
    manifest = {
        "source_root": str(source.resolve()),
        "source_commit": source_commit,
        "problems": list(names),
        "files": digests,
    }
    path = dest / MANIFEST_NAME
    path.write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    return path


def main(argv: list[str] | None = None) -> int:
    """Derive the catalog named on the command line. 0 on success, 2 on a refusal."""
    # A one-line description: argparse reflows the module docstring's literal block into prose.
    parser = argparse.ArgumentParser(prog="scb_cumulative_catalog", description=_SUMMARY)
    parser.add_argument("--source", required=True, type=Path, help="root of the shipped catalog")
    parser.add_argument("--dest", required=True, type=Path, help="root of the derived catalog")
    parser.add_argument("--source-commit", required=True, help="commit the source is read at; recorded verbatim")
    parser.add_argument(
        "--problem", action="append", required=True, metavar="NAME", help="problem to derive (repeatable)"
    )
    args = parser.parse_args(argv)
    try:
        manifest = build_catalog(
            Path(str(args.source)),
            Path(str(args.dest)),
            problems=[str(name) for name in cast("list[str]", args.problem)],
            source_commit=str(args.source_commit),
        )
    except CatalogError as exc:
        print(f"scb_cumulative_catalog: {exc}", file=sys.stderr)  # noqa: T201 - the refusal must be seen
        return 2
    print(str(manifest))  # noqa: T201 - the path IS this script's output
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
