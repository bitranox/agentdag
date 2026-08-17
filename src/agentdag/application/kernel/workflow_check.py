"""Refuse a workflow module that reaches for the clock or randomness (design 3.3).

A workflow program is coordinator code, and coordinator code is re-executed from the top
on every resume. A value taken from ``time``, ``datetime``, ``random``, ``uuid``,
``os.urandom`` or ``secrets`` would therefore differ between the first run and the replay,
and since such a value normally reaches a node's ``input.json``, it would silently change
that node's journal key and re-dispatch work the journal already paid for. The check is
static (an AST walk of the module's source), so it costs nothing at run time and refuses
before the first dispatch rather than after it.

The walk resolves ``import`` and ``from ... import`` ALIASES first, so ``import datetime
as d; d.datetime.now()``, ``from datetime import datetime as dt; dt.now()``, ``from time
import monotonic; monotonic()`` and ``from uuid import uuid4; uuid4()`` are all caught
exactly like their literal spellings - a module cannot dodge the check by choosing a
different name for the same forbidden call.

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
    from collections.abc import Mapping
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
    """Collect every forbidden call in ``tree``, wherever it sits (module level, function, method).

    Every call's dotted name is resolved through the module's import aliases first (see
    :func:`_alias_map`), so ``dt.now()`` is judged as ``datetime.datetime.now`` when the
    module imported it that way, not left as the literal, unmatchable ``dt.now``.
    """
    aliases = _alias_map(tree)
    found: list[_Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if name is None:
            continue  # a call-on-a-call or another chain no name-based rule can judge: skip, don't crash
        resolved = _resolve(name, aliases)
        if _is_forbidden(resolved):
            found.append(_Violation(name=resolved, lineno=node.lineno, col_offset=node.col_offset))
    return found


def _alias_map(tree: ast.AST) -> dict[str, str]:
    """Map every local name ``tree``'s imports bind to the canonical dotted path it reaches.

    ``import datetime as d`` binds ``d`` -> ``datetime``; ``from uuid import uuid4``
    binds ``uuid4`` -> ``uuid.uuid4``; ``from datetime import datetime as dt`` binds
    ``dt`` -> ``datetime.datetime``. A plain ``import time`` binds ``time`` -> ``time``
    too, which is a no-op for resolution but keeps the map total over every name the
    module's imports introduce.

    Args:
        tree: The parsed module.

    Returns:
        Local name -> canonical dotted path, folded over every import in the module
        (module level or nested), later imports of the same local name winning.

    Example:
        >>> import ast
        >>> tree = ast.parse("from uuid import uuid4\\nimport datetime as d\\n")
        >>> sorted(_alias_map(tree).items())
        [('d', 'datetime'), ('uuid4', 'uuid.uuid4')]
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _resolve(name: str, aliases: Mapping[str, str]) -> str:
    """Rewrite a dotted call name's ROOT segment through ``aliases``.

    Only the root is looked up: ``dt.now`` resolves through ``dt``, not through
    ``dt.now`` as a whole, so the rest of the chain (``.now``) carries over onto
    whatever the root's import bound it to.

    Args:
        name: A dotted call name as :func:`_dotted_name` renders it.
        aliases: The module's import alias map (see :func:`_alias_map`).

    Returns:
        ``name`` unchanged if its root is not an aliased or ``from``-imported local
        name (e.g. ``co.clock.now``, or a name the module never imported); otherwise
        the root replaced by what it canonically refers to.

    Example:
        >>> _resolve("dt.now", {"dt": "datetime.datetime"})
        'datetime.datetime.now'
        >>> _resolve("uuid4", {"uuid4": "uuid.uuid4"})
        'uuid.uuid4'
        >>> _resolve("co.clock.now", {"dt": "datetime.datetime"})
        'co.clock.now'
    """
    root, sep, rest = name.partition(".")
    canonical = aliases.get(root)
    if canonical is None:
        return name
    return f"{canonical}{sep}{rest}"


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
