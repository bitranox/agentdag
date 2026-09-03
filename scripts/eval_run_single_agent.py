"""Single-agent arm, stopped by the first saturation or by the protocol's ceilings.

Usage, from the repo root::

    .venv/bin/python scripts/eval_run_single_agent.py --task-file F --workspace DIR \\
        --ceiling N --deadline S --gate CMD --case spec --agentswarm-python P --max-score N

Charges NEW tokens only - ``input_tokens + cache_creation_input_tokens``, keyed by message id -
per `agentswarm/docs/evaluation-protocol.md`. Cache reads are excluded and output is not
charged, so the figure tracks work done rather than how long the conversation got.

Round 2 measures the cost at the FIRST crossing of the case's full score, so a CHECKPOINT is
taken after each prompt cycle and the arm stops the moment one reads a full score. Checkpointing
per prompt rather than on the clock matches the token accounting, which only settles when a
response completes. The agent is never left to decide it is finished: it is re-prompted
while budget remains, so every arm ends on a stop condition this script names.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from eval_checkpoint import CaseScorer, CheckpointRun

# The agent is RE-PROMPTED while budget remains, because the protocol's stop condition is the
# ceiling and not the system judging itself finished. Round 1 of this arm ended its first turn
# after 100,567 of 600,000 tokens having changed nothing, so without this the single agent is
# denied the very mechanism the coordinator arm used: agentdag re-dispatched two of its nodes
# as continuations. The prompt adds no information about the task - it says only that budget
# remains - and the agent keeps its whole context, which is an advantage over a continuation.
KEEP_GOING = "You still have budget remaining. Keep working on the task."


def _new_tokens(usage: dict[str, Any]) -> int:
    """The protocol's charge for one usage block: new context only."""
    return int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="eval_run_single_agent", description=__doc__)
    ap.add_argument("--task-file", type=Path, required=True)
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--ceiling", type=int, required=True, help="new-token ceiling for the arm")
    ap.add_argument("--deadline", type=float, required=True, help="wall-clock ceiling in seconds")
    ap.add_argument("--gate", required=True, help="the case's visible gate, run once at the end")
    ap.add_argument("--case", required=True, help="the agentswarm case whose hidden suite scores this")
    ap.add_argument("--agentswarm-python", type=Path, required=True)
    ap.add_argument(
        "--scorer-pythonpath",
        type=Path,
        default=None,
        help="source tree the scorer imports from; pin it so nothing moves it mid-arm",
    )
    ap.add_argument("--max-score", type=int, required=True)
    ap.add_argument("--arm", default="single_agent", help="the arm's name, recorded in the envelope")
    return ap.parse_args(argv)


class Spend:
    """New tokens charged so far, keyed by message id.

    Keyed rather than summed per event: the CLI repeats one message's usage once per content
    block, so a naive sum over events inflates the figure by about 1.5x.
    """

    def __init__(self) -> None:
        self.charged: dict[str, int] = {}
        self.cache_read = 0
        self.output = 0

    def add(self, message: object) -> None:
        raw = getattr(message, "usage", None)
        if not isinstance(raw, dict):
            return
        # The SDK types usage loosely, so isinstance narrows only to dict[Unknown, Unknown];
        # the cast declares the wire shape being read.
        usage = cast("dict[str, Any]", raw)
        mid = getattr(message, "message_id", None) or getattr(message, "uuid", None) or str(id(message))
        self.charged[str(mid)] = _new_tokens(usage)
        self.cache_read += int(usage.get("cache_read_input_tokens", 0))
        self.output += int(usage.get("output_tokens", 0))

    @property
    def total(self) -> int:
        return sum(self.charged.values())


def stop_reason(spend: Spend, checkpoints: CheckpointRun, args: argparse.Namespace, started: float) -> str | None:
    """The reason to stop after a completed prompt cycle, or None to prompt again."""
    if checkpoints.crossed:
        return "saturated"
    if spend.total >= int(args.ceiling):
        return f"token ceiling reached at {spend.total}"
    if time.monotonic() - started > float(args.deadline):
        return "wall-clock deadline reached"
    return None


async def drive(args: argparse.Namespace, checkpoints: CheckpointRun, started: float) -> tuple[Spend, int, str]:
    """Prompt, re-prompt and checkpoint until a stop condition fires."""
    spend = Spend()
    turns = 0
    reason = "no prompt cycle completed"
    options = ClaudeAgentOptions(
        cwd=str(args.workspace), permission_mode="bypassPermissions", setting_sources=[], model="sonnet"
    )
    async with ClaudeSDKClient(options=options) as client:
        prompt = Path(str(args.task_file)).read_text(encoding="utf-8").strip()
        while True:
            await client.query(prompt)
            turns += 1
            async for message in client.receive_response():
                spend.add(message)
            checkpoints.check(label=f"prompt-{turns}", new_tokens=spend.total)
            found = stop_reason(spend, checkpoints, args, started)
            if found is not None:
                reason = found
                break
            prompt = KEEP_GOING
    return spend, turns, reason


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workspace = Path(str(args.workspace))
    started = time.monotonic()
    checkpoints = CheckpointRun(
        scorer=CaseScorer(
            python=Path(str(args.agentswarm_python)),
            case=str(args.case),
            pythonpath=args.scorer_pythonpath,
        ),
        workspace=workspace,
        max_score=int(args.max_score),
        started=started,
    )

    spend, turns, reason = await drive(args, checkpoints, started)

    elapsed = time.monotonic() - started
    # nosec B603 / noqa S603 - the gate command is supplied by the operator on argv; running it
    # is the entire job of this line, and there is no shell.
    result = subprocess.run(  # noqa: S603
        str(args.gate).split(),
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    print(  # noqa: T201 - the JSON envelope IS this script's output
        json.dumps(
            {
                "arm": str(args.arm),
                "new_tokens": spend.total,
                "cache_read": spend.cache_read,
                "output_tokens": spend.output,
                "wall_s": round(elapsed, 1),
                "stop_reason": reason,
                "prompts_sent": turns,
                "gate_rc": result.returncode,
                "gate_tail": (result.stdout + result.stderr)[-300:],
                "workspace": str(workspace),
                "checkpoints": checkpoints.as_dict(),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
