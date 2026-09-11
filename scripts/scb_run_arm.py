# /// script
# requires-python = ">=3.12"
# ///
"""Run one SlopCodeBench arm, one problem at a time, refusing a problem the token cannot cover.

The harness freezes ``CLAUDE_CODE_OAUTH_TOKEN`` into the container at launch and nothing inside
refreshes it, so a problem that outlives the token dies mid-run and is VOID under the
pre-registration. Rather than let that happen unattended, the launcher reads the token's expiry
before each problem and stops the chain - exit 3, nothing started - when the remaining validity
is shorter than the problem's expected duration with a margin. The operator refreshes and
relaunches with the remaining problems.

Per problem it records the host load before and after (``/proc/loadavg``), the token expiry at
launch, and the harness's own exit code, all into the arm's log directory, because the
pre-registration asks for those as conditions rather than leaving them to memory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["ProblemPlan", "Waiting", "await_token", "parse_problem", "token_covers"]

EXIT_REFRESH_NEEDED = 3
MARGIN = 1.2
POLL_SECONDS = 300.0
"""How often the wait re-reads the credential. A token roll is not an event we can subscribe to."""


class ProblemPlan:
    """A problem to run and how long it is expected to take, from a ``name:seconds`` argument."""

    def __init__(self, name: str, expected_seconds: int) -> None:
        self.name = name
        self.expected_seconds = expected_seconds


def parse_problem(text: str) -> ProblemPlan:
    """Parse ``name:seconds``; the seconds are the calibration arm's duration for that problem."""
    name, sep, seconds = text.partition(":")
    if not sep or not name or not seconds.isdigit():
        raise argparse.ArgumentTypeError(f"expected NAME:SECONDS, got {text!r}")
    return ProblemPlan(name, int(seconds))


def token_covers(*, expires_at_ms: int, now_s: float, expected_s: int, margin: float = MARGIN) -> bool:
    """True when the token outlives the expected duration times the margin."""
    remaining = expires_at_ms / 1000.0 - now_s
    return remaining >= expected_s * margin


class Waiting:
    """The clock a wait runs on, bundled so the three cannot be passed apart.

    They are one decision - how this wait perceives and spends time - and a test that replaced the
    clock but not the sleep, or either without the interval, would wait on a mixture of real and
    fake time without anything saying so.
    """

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        poll_s: float = POLL_SECONDS,
    ) -> None:
        self.now = now
        self.sleep = sleep
        self.poll_s = poll_s


def await_token(
    plan: ProblemPlan,
    *,
    credentials: Path,
    deadline_s: float,
    waiting: Waiting | None = None,
    note: Callable[[str], None] | None = None,
) -> tuple[str, int] | None:
    """The credential once it covers ``plan``, or ``None`` when the wait ran out.

    The credential is RE-READ on every poll, because a token ROLL is exactly what this waits for:
    the file changes under it and the remaining validity jumps. Measured 2026-09-11, a real token
    sat at 0.14 h, rolled to 7.98 h, and a wait caught it within five minutes.

    Giving up returns ``None`` rather than the stale credential, so a caller cannot accidentally
    start a problem the token still cannot cover - which is the whole reason the guard exists.

    ``now`` and ``sleep`` are injected so the wait is testable without one.
    """
    clock = Waiting() if waiting is None else waiting
    while True:
        token, expires_at_ms = _read_credential(credentials)
        if token_covers(expires_at_ms=expires_at_ms, now_s=clock.now(), expected_s=plan.expected_seconds):
            return token, expires_at_ms
        if clock.now() >= deadline_s:
            return None
        if note is not None:
            remaining_h = (expires_at_ms / 1000.0 - clock.now()) / 3600.0
            note(f"WAITING for {plan.name}: token has {remaining_h:.2f} h, needs {plan.expected_seconds * MARGIN:.0f}s")
        # Never sleep past our own deadline: a short wait must give up when it said it would,
        # not one poll interval later. Found by running the real argv - `--wait-for-token 1`
        # slept the full 300 s - which the injected-sleep tests cannot see.
        clock.sleep(min(clock.poll_s, max(0.0, deadline_s - clock.now())))


def _read_credential(path: Path) -> tuple[str, int]:
    """The OAuth access token and its expiry in epoch milliseconds, never printed."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    oauth = payload["claudeAiOauth"]
    return str(oauth["accessToken"]), int(oauth["expiresAt"])


def _loadavg() -> str:
    return Path("/proc/loadavg").read_text(encoding="utf-8").strip()


def _note(log: Path, line: str) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {line}\n")


def _run_problem(plan: ProblemPlan, *, args: argparse.Namespace, token: str, log_dir: Path) -> int:
    log = log_dir / f"{plan.name}.log"
    env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token, "PYTHONUNBUFFERED": "1"}
    if args.problems_path is not None:
        env["SCBENCH_PROBLEMS_PATH"] = str(args.problems_path)
    command = ["uv", "run", "slop-code", "run", "--config", str(args.config), "--problem", plan.name]
    with log.open("ab") as out:
        completed = subprocess.run(  # noqa: S603 - fixed argv built here, no shell, token only in env
            command, cwd=args.harness, env=env, stdout=out, stderr=subprocess.STDOUT, check=False
        )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one SlopCodeBench arm one problem at a time, guarding the token window."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--harness", type=Path, required=True, help="the slop-code-bench checkout to run from")
    parser.add_argument("--problems-path", type=Path, default=None, help="SCBENCH_PROBLEMS_PATH for a derived catalog")
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, default=Path.home() / ".claude" / ".credentials.json")
    parser.add_argument(
        "--problem", type=parse_problem, action="append", required=True, help="NAME:EXPECTED_SECONDS, in run order"
    )
    parser.add_argument(
        "--wait-for-token",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="wait up to this long for the token to cover a problem, rather than refusing at once",
    )
    args = parser.parse_args(argv)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    chain = args.log_dir / "arm.log"
    for plan in args.problem:
        found = await_token(
            plan,
            credentials=args.credentials,
            deadline_s=time.time() + float(args.wait_for_token),
            note=lambda line: _note(chain, line),
        )
        if found is None:
            _, expires_at_ms = _read_credential(args.credentials)
            expiry = dt.datetime.fromtimestamp(expires_at_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
            needed = f"{plan.expected_seconds}s x {MARGIN}"
            _note(chain, f"REFRESH NEEDED before {plan.name}: token expires {expiry}, expected {needed}")
            return EXIT_REFRESH_NEEDED
        token, expires_at_ms = found
        expiry = dt.datetime.fromtimestamp(expires_at_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        _note(chain, f"START {plan.name} token_expires={expiry} loadavg={_loadavg()}")
        rc = _run_problem(plan, args=args, token=token, log_dir=args.log_dir)
        _note(chain, f"END {plan.name} rc={rc} loadavg={_loadavg()}")
        if rc != 0:
            return rc
    _note(chain, "ARM COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
