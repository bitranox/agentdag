"""WorkPort over the Claude Agent SDK: one isolated client per node.

Each node gets its own client, its own working tree and its own agent configuration
directory, and ``setting_sources=[]`` keeps the coordinator's own project settings out
of the node's context - the node is told what to do by the brief alone.

The SDK MERGES :attr:`~claude_agent_sdk.ClaudeAgentOptions.env` into the inherited
process environment rather than replacing it, so overriding ``HOME`` costs the child
nothing else. What it does cost is the login: the CLI reads that from its configuration
directory, and an empty one answers "Not logged in". ``CLAUDE_CONFIG_DIR`` points the
node at a directory under the run store holding its OWN copy of the credential, so a
token refresh lands in the node's copy and never in the operator's file, and N parallel
nodes never share one file. ``HOME`` is overridden as well, because a Node program reads
``USERPROFILE`` rather than ``HOME`` on Windows and neither is the credential knob.

The model call itself is the one genuinely external edge in graph A, so it is not
unit-tested: an exception there is turned into a failed node, never allowed past the
branch, and the attended scratch-fleet run is what exercises it. The credential copy IS
unit-tested, at the constructor seam that injects the source path.

Contents:
    * :class:`ClaudeSdkWork` - the port implementation.
"""

from __future__ import annotations

import os
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from ...domain.graph_a import WorkResult

__all__ = ["ClaudeSdkWork"]

_PROMPT = (
    "Apply the change described in your system prompt to this repository. Commit with a clear message. Do not push."
)
_TOOLS = ["Read", "Edit", "Write", "Bash", "Grep", "Glob"]
_CONFIG_DIR_NAME = ".claude"
_CREDENTIALS_NAME = ".credentials.json"
_OWNER_ONLY = 0o600


def _default_credentials_path() -> Path:
    """Return the operator's own Claude credential file.

    Returns:
        ``~/.claude/.credentials.json``, whether or not it exists.
    """
    return Path.home() / _CONFIG_DIR_NAME / _CREDENTIALS_NAME


class ClaudeSdkWork:
    """Run one work node as a Claude Agent SDK client."""

    def __init__(self, *, max_turns: int = 25, credentials_source: Path | None = None) -> None:
        """Bound how long a node may run and say where its login is copied from.

        Args:
            max_turns: Turn ceiling handed to the SDK; the baseline has no spend cap,
                so this is the only bound on a node.
            credentials_source: File the per-node credential copy is made from;
                defaults to the operator's own. Injected so the copy is testable
                without reading the operator's real login.
        """
        self._max_turns = max_turns
        self._credentials_source = credentials_source or _default_credentials_path()

    def prepare_config_dir(self, home: Path) -> Path:
        """Give the node its own agent configuration directory, with its own credential.

        The copy is created with ``O_EXCL`` and mode ``0600`` in one step rather than
        written and then restricted, so the secret is never briefly world-readable. An
        existing copy is left alone (the node may have refreshed its token into it), and
        an absent source is left alone too: the node then fails with the CLI's own "not
        logged in" message, which is the honest outcome.

        Args:
            home: The node's isolated home directory.

        Returns:
            The directory to hand the child as ``CLAUDE_CONFIG_DIR``.
        """
        config_dir = home / _CONFIG_DIR_NAME
        _copy_credential(self._credentials_source, config_dir / _CREDENTIALS_NAME)
        return config_dir

    async def run(self, worktree: Path, brief: str, model: str, home: Path) -> WorkResult:
        """Run the node against ``worktree`` and report what it did.

        Args:
            worktree: The working tree the node may change.
            brief: The change to make, handed over as the node's system prompt.
            model: The model to run on.
            home: An isolated home directory for this node's agent state.

        Returns:
            A typed record of the run: never the node's prose.
        """
        config_dir = self.prepare_config_dir(home)
        options = ClaudeAgentOptions(
            cwd=str(worktree),
            system_prompt=brief,
            setting_sources=[],
            model=model,
            max_turns=self._max_turns,
            permission_mode="acceptEdits",
            allowed_tools=_TOOLS,
            env={"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(_PROMPT)
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        return _to_result(message)
        except Exception as exc:
            # The external edge is caught broadly on purpose: whatever the SDK or the
            # network does, it becomes a failed NODE, never a failed run.
            return WorkResult(ok=False, error=repr(exc))
        return WorkResult(ok=False, error="no ResultMessage")


def _copy_credential(source: Path, destination: Path) -> None:
    """Copy ``source`` to ``destination`` once, owner-only, never overwriting.

    Args:
        source: The credential to copy; a missing one is not an error.
        destination: Where the node's own copy goes.
    """
    if not source.is_file():
        return
    payload = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _OWNER_ONLY)
    except FileExistsError:
        return
    with os.fdopen(handle, "wb") as opened:
        opened.write(payload)


def _to_result(message: ResultMessage) -> WorkResult:
    """Translate the SDK's result message into the domain record.

    Args:
        message: The SDK's terminal message for one query.

    Returns:
        The typed work result the graph branches on.
    """
    usage = message.usage or {}
    return WorkResult(
        ok=not message.is_error,
        num_turns=message.num_turns,
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cost_usd=message.total_cost_usd,
    )
