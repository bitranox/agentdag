"""WorkPort over the Claude Agent SDK: one isolated client per node.

Each node gets its own client, its own working tree and its own home directory, and
``setting_sources=[]`` keeps the coordinator's own project settings out of the node's
context - the node is told what to do by the brief alone.

The SDK MERGES :attr:`~claude_agent_sdk.ClaudeAgentOptions.env` into the inherited
process environment rather than replacing it, so overriding ``HOME`` costs the child
nothing else. What it does cost is the login: the CLI reads that from
``$HOME/.claude/.credentials.json``, and an empty home answers "Not logged in". The
credential is therefore linked into the node's home, and nothing else is.

This is the one genuinely external edge in graph A, so it is not unit-tested: an
exception here is turned into a failed node, never allowed past the branch, and the
attended scratch-fleet run is what exercises it.

Contents:
    * :class:`ClaudeSdkWork` - the port implementation.
"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from ...domain.graph_a import WorkResult

__all__ = ["ClaudeSdkWork"]

_PROMPT = (
    "Apply the change described in your system prompt to this repository. Commit with a clear message. Do not push."
)
_TOOLS = ["Read", "Edit", "Write", "Bash", "Grep", "Glob"]
_CREDENTIALS_REL = Path(".claude") / ".credentials.json"


class ClaudeSdkWork:
    """Run one work node as a Claude Agent SDK client."""

    def __init__(self, *, max_turns: int = 25) -> None:
        """Bound how long a node may run.

        Args:
            max_turns: Turn ceiling handed to the SDK; the baseline has no spend cap,
                so this is the only bound on a node.
        """
        self._max_turns = max_turns

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
        _link_credentials_into(home)
        options = ClaudeAgentOptions(
            cwd=str(worktree),
            system_prompt=brief,
            setting_sources=[],
            model=model,
            max_turns=self._max_turns,
            permission_mode="acceptEdits",
            allowed_tools=_TOOLS,
            env={"HOME": str(home)},
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


def _link_credentials_into(home: Path) -> None:
    """Make the operator's Claude login reachable from an isolated agent home.

    A symlink is preferred so the secret stays in exactly one place; where the platform
    refuses one (Windows without the symlink privilege) a copy is made instead and
    restricted to the owner. Absent credentials are left alone: the node then reports a
    failed run with the CLI's own message, which is the honest outcome.

    Args:
        home: The node's home directory.
    """
    source = Path.home() / _CREDENTIALS_REL
    link = home / _CREDENTIALS_REL
    if link.is_symlink() or link.exists() or not source.is_file():
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(source)
    except OSError:
        link.write_bytes(source.read_bytes())
        link.chmod(0o600)


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
