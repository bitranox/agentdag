"""Run agentdag's `plan-goal` under the protocol's ceiling, counted in the protocol's units.

Usage, from the repo root::

    .venv/bin/python scripts/eval_run_agentdag.py GOAL_FILE RUNS_DIR CEILING DEADLINE_S LOG

Prints a JSON envelope on stdout: new_tokens, cache_read, output_tokens, wall_s, stop_reason,
stopped_early, run_dir and the workspace to score.

agentdag cannot enforce this ceiling itself. Its own per-node cap and run-wide row ceiling are
computed from `charged_tokens`, which is `input_total + output` with `input_total` including
`cache_read_input_tokens` - measured at 17.0x the protocol's new-token figure on a real node.
Setting its budget to 600,000 would therefore stop the run at roughly a seventeenth of the
intended spend, so the ceiling is applied from outside, over the same transcripts the report
is computed from.

Polls the run directory, sums new tokens per finished dispatch, and terminates the coordinator
on the first crossing of the ceiling or the deadline.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

POLL_S = 5.0


def new_tokens(run_dir: Path) -> tuple[int, int, int]:
    """Return (new_tokens, cache_read, output) summed over every finished dispatch.

    One ``ResultMessage`` per dispatch carries that dispatch's cumulative usage, so reading
    only those is already keyed by dispatch and cannot double-count the repeated per-content
    block usage the CLI emits.
    """
    new = cache = out = 0
    for transcript in run_dir.rglob("nodes/*/*/transcript.jsonl"):
        for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                message: dict[str, Any] = json.loads(line)
            except ValueError:
                continue
            raw = message.get("usage")
            if message.get("type") == "ResultMessage" and isinstance(raw, dict):
                # json.loads returns Any, so isinstance narrows only to dict[Unknown, Unknown];
                # the cast is where this file declares the wire shape it is reading.
                usage = cast("dict[str, Any]", raw)
                new += int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
                cache += int(usage.get("cache_read_input_tokens", 0))
                out += int(usage.get("output_tokens", 0))
    return new, cache, out


def main() -> int:
    goal_file, runs_dir, ceiling_s, deadline_s, log_path = sys.argv[1:6]
    ceiling, deadline = int(ceiling_s), float(deadline_s)
    goal = Path(goal_file).read_text(encoding="utf-8")
    runs = Path(runs_dir)
    runs.mkdir(parents=True, exist_ok=True)

    argv = [
        ".venv/bin/python",
        "-m",
        "agentdag",
        "run",
        "start",
        "plan-goal",
        "--arg",
        f"goal={goal}",
        "--runs",
        str(runs),
        "--foreground",
    ]
    started = time.monotonic()
    with Path(log_path).open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)  # noqa: S603
        reason = "coordinator exited on its own"
        while proc.poll() is None:
            time.sleep(POLL_S)
            run_dirs = [d for d in runs.iterdir() if d.is_dir()]
            spent = new_tokens(run_dirs[0])[0] if run_dirs else 0
            if spent >= ceiling:
                reason = f"token ceiling reached at {spent}"
                os.killpg(proc.pid, signal.SIGTERM)
                break
            if time.monotonic() - started > deadline:
                reason = "wall-clock deadline reached"
                os.killpg(proc.pid, signal.SIGTERM)
                break
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)

    run_dirs = [d for d in runs.iterdir() if d.is_dir()]
    run_dir = run_dirs[0] if run_dirs else runs
    new, cache, out = new_tokens(run_dir)
    print(  # noqa: T201 - the JSON envelope IS this script's output
        json.dumps(
            {
                "arm": "agentdag",
                "new_tokens": new,
                "cache_read": cache,
                "output_tokens": out,
                "wall_s": round(time.monotonic() - started, 1),
                "stop_reason": reason,
                "stopped_early": reason == "coordinator exited on its own",
                "run_dir": str(run_dir),
                "workspace": str(run_dir / "wt" / "root"),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
