"""Graph A (fleet migration) CLI commands.

Two commands, in the order they are used:

``agentdag graph-a scratch REAL_REPOS_FILE``
    Read a list of real repositories once and mirror each into ``<scratch>/origin``.
    The mirrors are the only push targets any later run will accept.

``agentdag graph-a run REPOS_FILE BRIEF_FILE``
    Run graph A over those mirrors and, after one console approval, push what passed.

Contents:
    * :func:`cli_graph_a` - the ``graph-a`` group.
    * :func:`cli_graph_a_scratch` - build the scratch fleet.
    * :func:`cli_graph_a_run` - run the graph.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import lib_log_rich.runtime
import rich_click as click

from agentdag.adapters.graph_a import GitCli
from agentdag.application.graph_a import make_scratch_fleet, run_graph
from agentdag.domain.graph_a import parse_repos_text

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..context import get_cli_context
from ..exit_codes import ExitCode
from ..typed_click import argument, option

logger = logging.getLogger(__name__)

DEFAULT_SCRATCH = Path(tempfile.gettempdir()) / "agentdag-scratch"
"""Where the scratch fleet lives; never inside a real repository."""

DEFAULT_RUNS = Path(tempfile.gettempdir()) / "agentdag-baseline"
"""Where each run keeps its worktrees, logs and records."""

DEFAULT_LOCK = Path(tempfile.gettempdir()) / "agentdag-bmk-tool-env.lock"
"""Host-wide lock serialising the gate: the build tool environment is shared."""

REPOS_FILE_NAME = "REPOS.txt"
"""Name of the list ``scratch`` writes and ``run`` reads."""


@click.group("graph-a", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_graph_a() -> None:
    """Run the graph A fleet migration against scratch clones."""


@click.command("scratch", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("real_repos_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@option(
    "--scratch",
    "scratch",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_SCRATCH,
    show_default=True,
    help="Directory the scratch fleet is built in.",
)
def cli_graph_a_scratch(real_repos_file: Path, scratch: Path) -> None:
    """Mirror every repository listed in REAL_REPOS_FILE into the scratch fleet.

    The real repositories are read once, by ``git clone --mirror``, and are never
    written. The resulting list of mirrors is written to ``<scratch>/REPOS.txt``,
    which is the input of ``graph-a run``.
    """
    with lib_log_rich.runtime.bind(job_id="cli-graph-a-scratch", extra={"command": "graph-a scratch"}):
        real_repos = parse_repos_text(real_repos_file.read_text())
        logger.info("Building the scratch fleet", extra={"repos": len(real_repos), "scratch": str(scratch)})
        origins = make_scratch_fleet(real_repos, scratch, GitCli())
        listing = scratch / REPOS_FILE_NAME
        listing.write_text("".join(f"{origin}\n" for origin in origins))
        safe_console.echo(f"{len(origins)} scratch origins under {scratch / 'origin'}")
        safe_console.echo(str(listing))


@click.command("run", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("repos_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@argument("brief_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@option(
    "--scratch",
    "scratch",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_SCRATCH,
    show_default=True,
    help="Scratch directory owning the only permitted push targets.",
)
@option(
    "--runs",
    "runs",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_RUNS,
    show_default=True,
    help="Directory holding one timestamped directory per run.",
)
@option("--parallel", type=int, default=2, show_default=True, help="How many branches may run at once.")
@option("--model", type=str, default="sonnet", show_default=True, help="Model each work node runs on.")
@option(
    "--lock",
    "lock",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_LOCK,
    show_default=True,
    help="Host-wide lock file serialising the gate across branches.",
)
@click.pass_context
def cli_graph_a_run(
    ctx: click.Context,
    # Click invokes a callback with its parameters as keywords, so declaring them
    # keyword-only costs nothing and keeps the long option list readable.
    *,
    repos_file: Path,
    brief_file: Path,
    scratch: Path,
    runs: Path,
    parallel: int,
    model: str,
    lock: Path,
) -> None:
    """Run graph A over the scratch origins in REPOS_FILE with the change in BRIEF_FILE.

    Every repository gets its own worktree, its own work node and its own gate run.
    Nothing is pushed before the resulting push list has been approved on the console.

    Raises:
        SystemExit: With ``INVALID_ARGUMENT`` if a push target lies outside the
            scratch directory, which is a hard stop rather than a skipped repository.
    """
    services = get_cli_context(ctx).services
    extra = {"command": "graph-a run", "model": model, "parallel": parallel}
    with lib_log_rich.runtime.bind(job_id="cli-graph-a-run", extra=extra):
        origins = parse_repos_text(repos_file.read_text())
        brief = brief_file.read_text()
        wiring = services.wire_graph_a(runs=runs, lock=lock)
        safe_console.echo(f"run store: {wiring.store.root}")
        logger.info("Running graph A", extra={"repos": len(origins), "run_root": str(wiring.store.root)})
        try:
            rc = asyncio.run(
                run_graph(
                    origins=origins,
                    brief=brief,
                    model=model,
                    parallel=parallel,
                    scratch_root=scratch,
                    git=wiring.git,
                    gate=wiring.gate,
                    work=wiring.work,
                    approve=wiring.approve,
                    store=wiring.store,
                )
            )
        except ValueError as exc:
            safe_console.echo(str(exc))
            raise SystemExit(ExitCode.INVALID_ARGUMENT) from exc
        safe_console.echo(f"tally: {wiring.store.root / 'tally.json'}")
        if rc != 0:
            raise SystemExit(rc)


cli_graph_a.add_command(cli_graph_a_scratch)
cli_graph_a.add_command(cli_graph_a_run)


__all__ = ["cli_graph_a", "cli_graph_a_run", "cli_graph_a_scratch"]
