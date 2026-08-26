"""Strictly-typed wrappers for rich_click's partially-typed decorators.

rich_click ships ``py.typed``, but its ``argument``, ``option`` and
``version_option`` decorators are typed with a partially-unknown return, so the
strict type checker reports ``reportUnknownMemberType`` at every call site.
click's own decorators
are fully typed, but they default the parameter class to ``click.Option`` rather
than rich_click's ``RichOption``, which would change help rendering.

This module forwards to rich_click's decorators (so ``RichOption``/``RichArgument``
are still used at runtime) through a typed ``Protocol``: the ``cast`` is a no-op
at runtime, and the wrappers keep the exact same public signatures they always
had, so no rule needs to be silenced anywhere.

``get_text_stream`` is here for a different reason: click 8.5.0 deprecated it (removal
in 9.0) and routed it through a deprecation shim, so from that version its type reads as
unknown and every call site fails strict mode. Forwarding it through a Protocol keeps the
runtime call exactly as it was - it does NOT resolve to the same stream as a bare
``sys.stdout`` on every platform, so substituting one would be a behaviour change rather
than a typing fix. Migrating off it is owed before click 9.0.

Other click members (``command``, ``group``, ``echo``, ``Context``, ``Path`` ...)
type cleanly and are still used directly as ``click.X`` at call sites.
"""

from collections.abc import Callable
from typing import Any, Literal, Protocol, TextIO, cast

import rich_click as click

# Click decorators turn a command function (or another decorator's result) into a
# wrapped callable. ``Any`` is fully known to the type checker, so this alias stays
# clean and works both as ``@option(...)`` and as a value collected into a list.
_CommandDecorator = Callable[[Callable[..., Any]], Callable[..., Any]]


class _RichClickDecorators(Protocol):
    """rich_click's decorator surface, declared with complete types."""

    argument: Callable[..., _CommandDecorator]
    option: Callable[..., _CommandDecorator]
    version_option: Callable[..., _CommandDecorator]


class _RichClickStreams(Protocol):
    """click's stream accessor, declared with complete types (see module docstring)."""

    def get_text_stream(
        self,
        name: Literal["stdin", "stdout", "stderr"],
        encoding: str | None = None,
        errors: str | None = "strict",
    ) -> TextIO: ...


# ``cast`` is type-only; at runtime these forward to rich_click's own decorators,
# so ``RichOption``/``RichArgument`` behavior is unchanged.
_click = cast("_RichClickDecorators", click)
_streams = cast("_RichClickStreams", click)


def argument(*param_decls: str, **attrs: Any) -> _CommandDecorator:
    """Typed wrapper over :func:`rich_click.argument`. See module docstring."""
    return _click.argument(*param_decls, **attrs)


def option(*param_decls: str, **attrs: Any) -> _CommandDecorator:
    """Typed wrapper over :func:`rich_click.option`. See module docstring."""
    return _click.option(*param_decls, **attrs)


def version_option(*param_decls: str, **attrs: Any) -> _CommandDecorator:
    """Typed wrapper over :func:`rich_click.version_option`. See module docstring."""
    return _click.version_option(*param_decls, **attrs)


def get_text_stream(name: Literal["stdin", "stdout", "stderr"]) -> TextIO:
    """Typed wrapper over click's ``get_text_stream``. See module docstring."""
    return _streams.get_text_stream(name)


__all__ = ["argument", "get_text_stream", "option", "version_option"]
