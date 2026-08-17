"""Refuse a workflow module that reaches for the clock or randomness (design 3.3).

A workflow program is coordinator code, and coordinator code is re-executed from the top
on every resume. A value taken from ``time``, ``datetime``, ``random``, ``uuid``,
``os.urandom`` or ``secrets`` would therefore differ between the first run and the replay,
and since such a value normally reaches a node's ``input.json``, it would silently change
that node's journal key and re-dispatch work the journal already paid for. The check is
static (an AST walk of the module's source), so it costs nothing at run time and refuses
before the first dispatch rather than after it.

What a workflow uses instead: the coordinator's injected clock (``co.clock.now()``) and
values read back from records, both of which replay identically.

Contents:
    * :func:`assert_deterministic` - refuse a module that calls a nondeterministic source.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...domain.errors import NondeterministicCallError

if TYPE_CHECKING:
    from types import ModuleType

__all__ = ["assert_deterministic"]

_FORBIDDEN_CALLS = frozenset(
    {
        "time.time",
        "time.monotonic",
        "time.perf_counter",
        "datetime.now",
        "datetime.utcnow",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "uuid.uuid4",
        "uuid.uuid1",
        "os.urandom",
    }
)
"""Fully dotted callables a workflow module may not call, whichever import spelling reaches them."""

_FORBIDDEN_MODULES = frozenset({"random", "secrets"})
"""Modules where EVERY attribute call is nondeterministic, so no member list can go stale."""


@dataclass(frozen=True, slots=True)
class _Violation:
    """One forbidden call found in a workflow module's source."""

    name: str
    lineno: int
    col_offset: int


def assert_deterministic(module: ModuleType) -> None:
    """Refuse ``module`` if it calls the clock or a randomness source.

    Args:
        module: The workflow module, as imported. Its source is read from a
            ``__source__`` attribute when it carries one (the seam a test uses for a
            module built in memory), otherwise from disk with :func:`inspect.getsource`.

    Raises:
        NondeterministicCallError: the module calls a forbidden source; the message names
            the call and the line, and the FIRST one in source order is reported.
        OSError: the module has no ``__source__`` and no readable source file.

    Example:
        >>> import types
        >>> module = types.ModuleType("wf")
        >>> module.__dict__["__source__"] = "def program(co, args):\\n    return co.clock.now()\\n"
        >>> assert_deterministic(module) is None
        True
    """
    found = _violations(ast.parse(_source_of(module)))
    if not found:
        return
    first = min(found, key=lambda violation: (violation.lineno, violation.col_offset))
    raise NondeterministicCallError(
        f"{first.name}() at line {first.lineno} is unavailable in coordinator code: "
        "it breaks resume; take the value from a record or the coordinator's clock"
    )


def _source_of(module: ModuleType) -> str:
    """Return ``module``'s source: its carried ``__source__`` when it has one, else the file on disk."""
    carried = module.__dict__.get("__source__")
    if isinstance(carried, str):
        return carried
    return inspect.getsource(module)


def _violations(tree: ast.AST) -> list[_Violation]:
    """Collect every forbidden call in ``tree``, wherever it sits (module level, function, method)."""
    found: list[_Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name is not None and _is_forbidden(name):
            found.append(_Violation(name=name, lineno=node.lineno, col_offset=node.col_offset))
    return found


def _dotted_name(node: ast.expr) -> str | None:
    """Render a call target as its dotted name, or ``None`` if it is not a plain name chain.

    Args:
        node: The ``func`` of an :class:`ast.Call`.

    Returns:
        ``"datetime.datetime.utcnow"`` for an attribute chain rooted in a plain name;
        ``None`` when the chain is rooted in anything else (a subscript, another call),
        which no name-based rule can judge.

    Example:
        >>> import ast
        >>> call = ast.parse("datetime.datetime.utcnow()").body[0].value
        >>> _dotted_name(call.func)
        'datetime.datetime.utcnow'
        >>> _dotted_name(ast.parse("f(x)()").body[0].value.func) is None
        True
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _is_forbidden(name: str) -> bool:
    """Return whether a dotted call name is one of the nondeterministic sources.

    A name in :data:`_FORBIDDEN_MODULES` matches every attribute call on it
    (``random.random``, ``random.choice``, ``secrets.token_hex``) but not a bare call of
    a local variable that happens to share the name.

    Example:
        >>> _is_forbidden("random.choice"), _is_forbidden("random"), _is_forbidden("co.clock.now")
        (True, False, False)
    """
    if name in _FORBIDDEN_CALLS:
        return True
    root, _, rest = name.partition(".")
    return bool(rest) and root in _FORBIDDEN_MODULES
