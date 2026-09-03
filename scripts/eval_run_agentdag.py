"""Run agentdag's `plan-goal` under the protocol's ceilings, stopped at the first saturation.

Usage, from the repo root::

    .venv/bin/python scripts/eval_run_agentdag.py --goal-file F --runs DIR --log F \\
        --ceiling N --deadline S --case spec --agentswarm-python P [--policy F]

Prints a JSON envelope on stdout: new_tokens, cache_read, output_tokens, wall_s, stop_reason,
run_dir, the workspace to score, and the checkpoint series.

Round 2 measures the cost at the FIRST crossing of the case's full score rather than the score
at a ceiling, because the case saturates. So this polls two things, not one. It sums new tokens
per finished dispatch and stops on the ceiling or the deadline, and it takes a CHECKPOINT each
time a node lands, stopping the moment one reads a full score.

Checkpointing on node landings rather than on the clock is not a convenience: the token figure
is only available per finished dispatch, so scoring more often would give a finer score timeline
against a coarser token timeline and could not locate a crossing's COST any better.

agentdag cannot enforce this ceiling itself. Its own per-node cap and run-wide row ceiling are
computed from `charged_tokens`, which is `input_total + output` with `input_total` including
`cache_read_input_tokens` - measured at 17.0x the protocol's new-token figure on a real node.
Setting its budget to 600,000 would therefore stop the run at roughly a seventeenth of the
intended spend, so the ceiling is applied from outside, over the same transcripts the report
is computed from.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

from eval_checkpoint import CaseScorer, CheckpointRun

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


def nodes_landed(run_dir: Path) -> int:
    """How many nodes have produced a record, read from the journal the coordinator appends.

    A trailing partial line is expected while the coordinator is writing and is skipped rather
    than treated as corruption.
    """
    journal = run_dir / "journal.jsonl"
    if not journal.is_file():
        return 0
    landed = 0
    for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        if entry.get("event") == "result":
            landed += 1
    return landed


def only_run_dir(runs: Path) -> Path | None:
    """The run directory this launch created, or None before the coordinator has made it."""
    found = [d for d in runs.iterdir() if d.is_dir()] if runs.is_dir() else []
    return found[0] if found else None


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="eval_run_agentdag", description=__doc__)
    ap.add_argument("--goal-file", type=Path, required=True)
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--ceiling", type=int, required=True, help="new-token ceiling for the arm")
    ap.add_argument("--deadline", type=float, required=True, help="wall-clock ceiling in seconds")
    ap.add_argument("--case", required=True, help="the agentswarm case whose hidden suite scores this")
    ap.add_argument("--agentswarm-python", type=Path, required=True)
    ap.add_argument(
        "--scorer-pythonpath",
        type=Path,
        default=None,
        help="source tree the scorer imports from; pin it so nothing moves it mid-arm",
    )
    ap.add_argument("--max-score", type=int, required=True)
    ap.add_argument("--policy", type=Path, default=None, help="an alternate tier policy for this arm")
    ap.add_argument("--arm", default="agentdag", help="the arm's name, recorded in the envelope")
    return ap.parse_args(argv)


def coordinator_argv(args: argparse.Namespace, goal: str) -> list[str]:
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
        str(args.runs),
        "--foreground",
    ]
    if args.policy is not None:
        argv += ["--policy", str(args.policy)]
    return argv


class Arm:
    """One coordinator launch, watched against three stop conditions."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.runs = Path(str(args.runs))
        self.scorer = CaseScorer(
            python=Path(str(args.agentswarm_python)),
            case=str(args.case),
            pythonpath=args.scorer_pythonpath,
        )
        self.checkpoints: CheckpointRun | None = None
        self.landed = 0
        self.started = time.monotonic()

    def attach(self, run_dir: Path) -> CheckpointRun:
        """Bind the instrument once the coordinator has minted its workspace."""
        if self.checkpoints is None:
            self.checkpoints = CheckpointRun(
                scorer=self.scorer,
                workspace=run_dir / "wt" / "root",
                max_score=int(self.args.max_score),
                started=self.started,
            )
        return self.checkpoints

    def stop_reason(self, run_dir: Path | None) -> str | None:
        """The reason to stop now, or None to keep going."""
        if run_dir is None:
            return None
        spent = new_tokens(run_dir)[0]
        landed = nodes_landed(run_dir)
        if landed > self.landed:
            self.landed = landed
            self.attach(run_dir).check(label=f"node-{landed}", new_tokens=spent)
        if self.checkpoints is not None and self.checkpoints.crossed:
            return "saturated"
        if spent >= int(self.args.ceiling):
            return f"token ceiling reached at {spent}"
        if time.monotonic() - self.started > float(self.args.deadline):
            return "wall-clock deadline reached"
        return None


def signal_group(pid: int, sig: int) -> None:
    """Signal the coordinator's whole PROCESS GROUP, not just the parent.

    It spawns node subprocesses, so terminating only the parent would leave those running past
    the ceiling this script exists to hold. The platform guard sits here rather than only in
    `main` because a refusal in another function narrows nothing for the type checker, and CI
    checks this file against Windows.
    """
    if sys.platform == "win32":  # pragma: no cover - main refuses before anything is spawned
        raise RuntimeError("signalling a process group is POSIX-only")
    os.killpg(pid, sig)


def watch(arm: Arm, proc: subprocess.Popen[bytes]) -> str:
    """Poll until a stop condition fires or the coordinator ends by itself."""
    while proc.poll() is None:
        time.sleep(POLL_S)
        reason = arm.stop_reason(only_run_dir(arm.runs))
        if reason is not None:
            signal_group(proc.pid, signal.SIGTERM)
            return reason
    return "coordinator exited on its own"


def main(argv: list[str] | None = None) -> int:
    # POSIX only, and stated as a refusal rather than left to fail at the kill. The ceiling is
    # enforced by signalling the coordinator's whole PROCESS GROUP - it spawns node subprocesses,
    # and terminating only the parent would leave those running past the ceiling this script
    # exists to hold. `os.killpg` and `signal.SIGKILL` do not exist on Windows and there is no
    # drop-in equivalent (it needs CREATE_NEW_PROCESS_GROUP plus CTRL_BREAK), so a port is real
    # work rather than a shim. The early exit also narrows the platform for the type checker,
    # which is what CI's Windows cell reads.
    if sys.platform == "win32":  # pragma: no cover - refused before anything is spawned
        print(  # noqa: T201 - the refusal must reach whoever ran this
            "eval_run_agentdag.py is POSIX-only: it holds the ceiling by signalling the "
            "coordinator's process group, which Windows has no equivalent for.",
            file=sys.stderr,
        )
        return 2
    args = parse_args(argv)
    goal = Path(str(args.goal_file)).read_text(encoding="utf-8")
    runs = Path(str(args.runs))
    runs.mkdir(parents=True, exist_ok=True)

    arm = Arm(args)
    with Path(str(args.log)).open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(  # noqa: S603 - argv built here, no shell
            coordinator_argv(args, goal), stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        reason = watch(arm, proc)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            signal_group(proc.pid, signal.SIGKILL)

    run_dir = only_run_dir(runs) or runs
    new, cache, out = new_tokens(run_dir)
    # A final checkpoint after the coordinator is gone: the last node's work is only on disk once
    # it has exited, and without this an arm that saturates on its final node reads as one that
    # never crossed.
    final = arm.attach(run_dir)
    final.check(label="final", new_tokens=new)
    print(  # noqa: T201 - the JSON envelope IS this script's output
        json.dumps(
            {
                "arm": str(args.arm),
                "new_tokens": new,
                "cache_read": cache,
                "output_tokens": out,
                "wall_s": round(time.monotonic() - arm.started, 1),
                "stop_reason": reason,
                "nodes_landed": arm.landed,
                "policy": str(args.policy) if args.policy else "shipped",
                "run_dir": str(run_dir),
                "workspace": str(run_dir / "wt" / "root"),
                "checkpoints": final.as_dict(),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
