"""Single-agent arm, stopped by the protocol's ceiling rather than by its own judgement.

Usage, from the repo root::

    .venv/bin/python scripts/eval_run_single_agent.py TASK_FILE WORKSPACE CEILING DEADLINE_S GATE_CMD

Charges NEW tokens only - ``input_tokens + cache_creation_input_tokens``, keyed by message id -
per `agentswarm/docs/evaluation-protocol.md`. Cache reads are excluded and output is not
charged, so the figure tracks work done rather than how long the conversation got.

The ceiling is checked as each message arrives and the client is closed on the first crossing,
which is what "the run ends on the token ceiling, not on the system deciding it is finished"
means for a single agent. If the agent stops earlier of its own accord, that is recorded as
``stopped_early`` and reported, never hidden.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


def _new_tokens(usage: dict[str, Any]) -> int:
    """The protocol's charge for one usage block: new context only."""
    return int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))


async def main() -> int:
    task = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    cwd = Path(sys.argv[2])
    ceiling = int(sys.argv[3])
    deadline_s = float(sys.argv[4])
    gate = sys.argv[5]

    started = time.monotonic()
    charged: dict[str, int] = {}
    cache_read = 0
    output = 0
    stopped_early = True
    reason = "agent finished"

    # The agent is RE-PROMPTED while budget remains, because the protocol's stop condition is the
    # ceiling and not the system judging itself finished. Round 1 of this arm ended its first turn
    # after 100,567 of 600,000 tokens having changed nothing, so without this the single agent is
    # denied the very mechanism the coordinator arm used: agentdag re-dispatched two of its nodes
    # as continuations. The prompt adds no information about the task - it says only that budget
    # remains - and the agent keeps its whole context, which is an advantage over a continuation.
    keep_going = "You still have budget remaining. Keep working on the task."
    options = ClaudeAgentOptions(cwd=str(cwd), permission_mode="bypassPermissions", setting_sources=[], model="sonnet")
    turns = 0
    async with ClaudeSDKClient(options=options) as client:
        prompt = task
        while True:
            await client.query(prompt)
            turns += 1
            async for message in client.receive_response():
                raw = getattr(message, "usage", None)
                if isinstance(raw, dict):
                    # The SDK types usage loosely, so isinstance narrows only to
                    # dict[Unknown, Unknown]; the cast declares the wire shape being read.
                    usage = cast("dict[str, Any]", raw)
                    mid = getattr(message, "message_id", None) or getattr(message, "uuid", None) or str(id(message))
                    # Keyed by message id: the CLI repeats one message's usage per content block,
                    # so a naive sum over events inflates by about 1.5x.
                    charged[str(mid)] = _new_tokens(usage)
                    cache_read += int(usage.get("cache_read_input_tokens", 0))
                    output += int(usage.get("output_tokens", 0))
            spent = sum(charged.values())
            if spent >= ceiling:
                stopped_early, reason = False, f"token ceiling reached at {spent}"
                break
            if time.monotonic() - started > deadline_s:
                stopped_early, reason = False, "wall-clock deadline reached"
                break
            prompt = keep_going

    elapsed = time.monotonic() - started
    # nosec B603 / noqa S603 - the gate command is supplied by the operator on argv; running it
    # is the entire job of this line, and there is no shell.
    result = subprocess.run(  # noqa: S603
        gate.split(), cwd=cwd, capture_output=True, text=True, timeout=900, check=False
    )
    print(  # noqa: T201 - the JSON envelope IS this script's output
        json.dumps(
            {
                "arm": "single_agent",
                "new_tokens": sum(charged.values()),
                "cache_read": cache_read,
                "output_tokens": output,
                "wall_s": round(elapsed, 1),
                "stopped_early": stopped_early,
                "stop_reason": reason,
                "prompts_sent": turns,
                "gate_rc": result.returncode,
                "gate_tail": (result.stdout + result.stderr)[-300:],
                "workspace": str(cwd),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
