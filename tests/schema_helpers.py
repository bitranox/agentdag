"""Shared JSON-schema loading and validation for the schema-conformance tests.

``load`` and ``validator`` used to live only in ``test_kernel_schemas.py``; a real-run
schema-conformance test (``test_kernel_run.py``) needs the exact same ``validator()``
build - the same registry, the same referenced schemas - so both import it from here
rather than one re-deriving it.

Contents:
    * :func:`load` - read one shipped schema by name.
    * :func:`validator` - a Draft202012Validator for one schema, with every other
      shipped schema registered so its ``$ref``s resolve.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING, Any, Protocol, cast

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

if TYPE_CHECKING:
    from referencing.jsonschema import Schema

__all__ = ["load", "validator"]


class _SchemaValidator(Protocol):
    """The one Draft202012Validator method a caller needs, typed.

    jsonschema's shipped stub declares ``validate(self, *args, **kwargs) -> None``,
    which pyright strict reports as partially unknown; this narrow facade defines
    the real signature instead of suppressing the diagnostic at every call site.
    """

    def validate(self, instance: Any) -> None: ...


def load(name: str) -> dict[str, Any]:
    """Read ``agentdag/schemas/<name>.schema.json`` and parse it."""
    return json.loads((files("agentdag.schemas") / f"{name}.schema.json").read_text())


def validator(name: str) -> _SchemaValidator:
    """Build a validator for schema ``name``, with every shipped schema registered so its ``$ref``s resolve."""
    reg: Registry[Schema] = Registry()
    for other in ("node-spec", "result-record", "journal-line", "approve-payload", "handover", "map-manifest"):
        s = load(other)
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
        reg = reg.with_resource(f"{other}.schema.json", Resource.from_contents(s))
    return cast("_SchemaValidator", Draft202012Validator(load(name), registry=reg))
