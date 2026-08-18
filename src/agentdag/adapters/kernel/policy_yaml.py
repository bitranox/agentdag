"""Load the tier policy table from YAML into a Policy port implementation (design 2.3).

``max_turns`` and ``deny_bash`` are NOT read from the YAML - they are the app config's own
``[kernel]`` values (Task 14, ``defaultconfig.d/60-kernel.toml``), passed in by the composition
root (Task 17). This keeps the two concerns separate: the policy VERSION (the content hash of
the table) never changes just because an operator raised the turn ceiling, and the turn ceiling
never has to be duplicated into every policy table an operator might swap in.

Contents:
    * :func:`load_policy` - read a policy YAML file once, hash its bytes, parse it into a
      :class:`~agentdag.domain.policy.PolicyTable`.
    * :class:`LoadedPolicy` - the :class:`~agentdag.application.kernel.ports.Policy` port this
      module builds over a loaded table.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import yaml

from ...application.kernel.ports import ResolvedRow
from ...domain.policy import PolicyTable, resolve_row

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ...domain.models import NodeSpec
    from ...domain.policy import Thresholds, TierRow

__all__ = ["LoadedPolicy", "load_policy"]


def load_policy(path: Path, *, max_turns: int = 25, deny_bash: Sequence[str] = ()) -> LoadedPolicy:
    """Read ``path`` once, content-hash its bytes, and parse it into a loaded policy port.

    ``yaml.safe_load`` only - ``yaml.load`` without an explicit ``Loader`` can construct
    arbitrary Python objects from the YAML and is never used here, policy table or not.

    Args:
        path: The policy YAML file to load.
        max_turns: The SDK turn ceiling every node's dispatch runs under; from the app config's
            ``[kernel]`` table, not this file. Defaults to the config's own default (25).
        deny_bash: The bash denylist every node's PreToolUse hook enforces; likewise from the
            app config, not this file. Defaults to empty (no denylist).

    Returns:
        A :class:`LoadedPolicy` whose ``version`` is the content hash of ``path``'s bytes - two
        runs report a comparable version exactly when their table's bytes are identical.

    Raises:
        yaml.YAMLError: ``path`` is not valid YAML.
        pydantic.ValidationError: the parsed YAML does not match
            :class:`~agentdag.domain.policy.PolicyTable` (an unknown key, a missing required
            field, or a wrong-shaped value).
    """
    raw = path.read_bytes()
    version = "sha256:" + hashlib.sha256(raw).hexdigest()
    data = yaml.safe_load(raw.decode("utf-8"))
    table = PolicyTable.model_validate(data)
    return LoadedPolicy(table=table, version=version, max_turns=max_turns, deny_bash=tuple(deny_bash))


class LoadedPolicy:
    """The :class:`~agentdag.application.kernel.ports.Policy` port over one loaded policy table."""

    def __init__(self, *, table: PolicyTable, version: str, max_turns: int, deny_bash: tuple[str, ...]) -> None:
        """Bind a loaded table to the two app-config limits every node dispatch reads.

        Args:
            table: The parsed policy table.
            version: The content hash of the YAML bytes this table was parsed from.
            max_turns: The SDK turn ceiling, from the app config - see :func:`load_policy`.
            deny_bash: The bash denylist, from the app config - see :func:`load_policy`.
        """
        self.table = table
        self.version = version
        self.max_turns = max_turns
        self.deny_bash = deny_bash
        self.tokens_per_row: Mapping[str, int] = table.run_limits.tokens_per_row
        self.rows: dict[str, TierRow] = {row.alias: row for row in table.models}
        self.thresholds: Thresholds = table.thresholds

    def resolve(self, spec: NodeSpec) -> ResolvedRow:
        """Resolve ``spec`` to a model row and executor (design 2.3).

        A spec with no ``tier_role`` takes its KIND's default role from the table
        (``kind_defaults[spec.kind].tier_role``) before resolving - a kind whose default role
        is ``None`` (every code kind, plus ``map``/``batch``, whose default belongs to their
        body nodes) never resolves a model row on its own, so a spec of one of those kinds with
        no explicit role AND no explicit model raises.

        Args:
            spec: The node spec to resolve.

        Returns:
            The resolved row's alias and executor.

        Raises:
            SpecRejected: see :func:`~agentdag.domain.policy.resolve_row`.
        """
        tier_role = spec.tier_role
        if tier_role is None:
            kind_default = self.table.kind_defaults.get(spec.kind.value)
            tier_role = kind_default.tier_role if kind_default is not None else None
        row = resolve_row(self.table, tier_role=tier_role, model=spec.model)
        return ResolvedRow(alias=row.alias, executor=row.executor)
