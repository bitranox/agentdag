"""Whole-spec validation including the one 2.4 rule that needs a filesystem (decision 10).

Design 2.4's rules are split by whether they need I/O. The pure ones live in
:mod:`agentdag.domain.validate` over values alone; ``brief_ref`` containment cannot, because
"resolving INSIDE the run store after realpath" is a question only the filesystem answers -
a spec's path may be lexically innocent and still lead out through a symlink.

This module is the single entry point a dispatcher calls, so no caller can check one list of
rules and miss the other. The resolution happens HERE, behind a port, rather than in the
caller: the insertion review found the shipped cwd containment guard already passes traversal
performed by its caller, and repeating that shape in validation would defeat the rule it is
named for.

Nothing calls this yet, and the reason is recorded in :mod:`agentdag.domain.validate`: 2.4
governs PLANNER-emitted specs, no planner exists, and applying it to graph A's hand-authored
specs would refuse its legitimate ``apply`` node. ``brief_ref`` is a step further from live
than that - measured 2026-08-22, nothing in ``src/`` assigns it, so every spec the running
code builds carries the empty default. The rule is built ahead of its producer deliberately:
containment that arrives after the thing it contains has already shipped is not containment.

Contents:
    * :func:`validate_dispatchable` - the pure rules plus ``brief_ref`` containment, one verdict.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from ...domain.validate import SpecVerdict, validate_spec

if TYPE_CHECKING:
    from pathlib import Path

    from ...domain.models import NodeSpec
    from ...domain.policy import RunLimits
    from ...domain.validate import SpecContext
    from .ports import PathResolver

__all__ = ["validate_dispatchable"]


def validate_dispatchable(
    spec: NodeSpec,
    *,
    limits: RunLimits,
    context: SpecContext | None = None,
    brief_root: Path,
    resolver: PathResolver,
) -> SpecVerdict:
    """Return every reason to refuse ``spec``, the pure rules and the path rule in one verdict.

    Args:
        spec: The planner-emitted spec to validate.
        limits: The run's ceilings and allowlists.
        context: What the pure rules need besides the spec; omitted leaves those rules silent
            and SKIPPED rather than guessing.
        brief_root: The directory ``brief_ref`` is relative to and must resolve inside. Design
            2.4 and the node-spec schema both say "the run store", which is the directory
            holding every run; pass the RUN's own root instead unless something needs a brief
            from another run, because that is the tighter bound and every other relative path
            the kernel writes is already run-root relative.
        resolver: How to realpath a path. Production passes
            :class:`~agentdag.adapters.kernel.path_resolver_os.OsPathResolver`.

    Returns:
        The reasons to refuse, plus the pure rules that could not run for want of context.
    """
    verdict = validate_spec(spec, limits=limits, context=context)
    reasons = _brief_ref_reasons(spec.brief_ref, brief_root=brief_root, resolver=resolver)
    if not reasons:
        return verdict
    return SpecVerdict(reasons=(*verdict.reasons, *reasons), skipped=verdict.skipped)


def _brief_ref_reasons(brief_ref: str, *, brief_root: Path, resolver: PathResolver) -> list[str]:
    """Return the reasons ``brief_ref`` names a file outside the root it must live under.

    An EMPTY ``brief_ref`` is vacuously satisfied rather than refused or reported as skipped:
    there is nothing to contain. Whether a given kind must carry a brief at all is a different
    question with a different owner - the node-spec schema already binds ``minLength: 1`` on a
    spec once it is persisted, and the coordinator composes its own brief text for the code
    kinds - so answering it here would decide it twice.

    Existence is NOT required, for the same reason: 2.4 states containment, and a brief_ref
    naming nothing that exists is a different failure with a different remedy (it fails at
    read time, in a place that can say which file was missing).

    Args:
        brief_ref: The spec's brief path.
        brief_root: The directory it is relative to and must resolve inside.
        resolver: How to realpath a path.

    Returns:
        Every reason to refuse it, empty when it is contained.
    """
    if not brief_ref:
        return []
    if _is_absolute_anywhere(brief_ref):
        return [f"brief_ref {brief_ref!r} is absolute; a spec names its brief relative to the run store"]
    resolved = resolver.resolve(brief_root / brief_ref)
    root = resolver.resolve(brief_root)
    if not resolved.is_relative_to(root):
        # Lexical containment is sound only because BOTH sides have been realpathed: no ``..``
        # and no symlink survives that, which is exactly what a raw relative_to would miss.
        return [f"brief_ref {brief_ref!r} resolves to {resolved}, outside the run store at {root}"]
    return []


def _is_absolute_anywhere(brief_ref: str) -> bool:
    """Return whether ``brief_ref`` is absolute under EITHER platform's path rules.

    A spec travels: one emitted on Linux may be validated on Windows. ``Path`` answers for the
    host it runs on only - ``PureWindowsPath("/etc/passwd").is_absolute()`` is ``False`` for
    want of a drive - so a POSIX-absolute path would slip past a bare check on Windows and a
    UNC path past one on Linux. Both forms are refused on both platforms.

    Args:
        brief_ref: The spec's brief path.

    Returns:
        Whether either platform would call it absolute.
    """
    return PurePosixPath(brief_ref).is_absolute() or PureWindowsPath(brief_ref).is_absolute()
