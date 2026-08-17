"""WorkPort over the Claude Agent SDK: one isolated client per node.

Each node gets its own client, its own working tree and its own home directory, and
``setting_sources=[]`` keeps the coordinator's own project settings out of the node's
context - the node is told what to do by the brief alone.

This is the one genuinely external edge in graph A, so it is not unit-tested: an
exception here is turned into a failed node, never allowed past the branch, and the
attended scratch-fleet run is what exercises it.

Contents:
    * :class:`ClaudeSdkWork` - the port implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from ...domain.graph_a import WorkResult

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ClaudeSdkWork"]

_PROMPT = (
    "Apply the change described in your system prompt to this repository. Commit with a clear message. Do not push."
)
_TOOLS = ["Read", "Edit", "Write", "Bash", "Grep", "Glob"]


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
