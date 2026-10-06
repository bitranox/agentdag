"""CLI entry point and execution wrapper.

Provides the main entry point used by console scripts and ``python -m``
execution, ensuring consistent error handling and traceback restoration.

Contents:
    * :func:`main` - Primary entry point for CLI execution.
"""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING

import click
import lib_cli_exit_tools
import lib_log_rich.runtime

from agentdag import __init__conf__

from .constants import TRACEBACK_SUMMARY_LIMIT, TRACEBACK_VERBOSE_LIMIT
from .context import (
    apply_traceback_preferences,
    restore_traceback_state,
    snapshot_traceback_state,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from agentdag.composition import AppServices


def _run_cli(argv: Sequence[str] | None, *, services_factory: Callable[[], AppServices]) -> int:
    """Execute the CLI with exception handling.

    Args:
        argv: Optional sequence of CLI arguments. None uses sys.argv.
        services_factory: Factory function that returns AppServices. Passed via ctx.obj.

    Returns:
        Exit code produced by the command.
    """
    from .root import cli  # noqa: PLC0415 - deferred: lazy-loads the command tree so importing main stays cheap

    # Use Click's native invocation with obj parameter since lib_cli_exit_tools.run_cli
    # doesn't support passing obj. We replicate its behavior while adding obj support.
    args = list(argv) if argv is not None else sys.argv[1:]

    try:
        # cli is a rich_click group whose own main() reimplements click's Command.main().
        # Under standalone_mode=False it catches the click.exceptions.Exit that ctx.exit()
        # raises and RETURNS its exit code, and it returns a callback's plain return value the
        # same way, so the exit code arrives here as a return value and never as an Exit.
        # The config and email commands therefore exit through ctx.exit().
        exit_code = cli.main(
            args=args,
            prog_name=__init__conf__.shell_command,
            obj=services_factory,
            standalone_mode=False,
        )
        return exit_code if isinstance(exit_code, int) else 0
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        # The run, graph-a and notify-test commands exit with ``raise SystemExit(ExitCode.X)``
        # after printing their own message, and catch that SystemExit internally where a
        # caller recovers from it, so an integer code is a deliberate exit, not a crash. Left
        # to the catch-all below it would print as "SystemExit: N" after the real message.
        if isinstance(exc.code, int):
            return exc.code
        return _report_exception(exc)
    except BaseException as exc:
        # Catch BaseException (not just Exception) to handle KeyboardInterrupt and all errors
        # at the CLI boundary. This ensures consistent error formatting via
        # lib_cli_exit_tools regardless of exception type. Intentional, not a bug.
        return _report_exception(exc)


def _report_exception(exc: BaseException) -> int:
    """Print the exception being handled through lib_cli_exit_tools and return its exit code.

    Call it only from inside the ``except`` block handling ``exc``: lib_cli_exit_tools
    formats the exception currently being handled, not the argument.

    Args:
        exc: The exception that ended the command.

    Returns:
        The exit code lib_cli_exit_tools maps ``exc`` to.
    """
    tracebacks_enabled = bool(getattr(lib_cli_exit_tools.config, "traceback", False))
    apply_traceback_preferences(tracebacks_enabled)
    length_limit = TRACEBACK_VERBOSE_LIMIT if tracebacks_enabled else TRACEBACK_SUMMARY_LIMIT
    lib_cli_exit_tools.print_exception_message(trace_back=tracebacks_enabled, length_limit=length_limit)
    return lib_cli_exit_tools.get_system_exit_code(exc)


def main(
    argv: Sequence[str] | None = None,
    *,
    restore_traceback: bool = True,
    services_factory: Callable[[], AppServices] | None = None,
) -> int:
    """Execute the CLI with error handling and return the exit code.

    Provides the single entry point used by console scripts and
    ``python -m`` execution so that behaviour stays identical across transports.

    Args:
        argv: Optional sequence of CLI arguments. None uses sys.argv.
        restore_traceback: Whether to restore prior traceback configuration after execution.
        services_factory: Factory function returning AppServices. Required.
            Callers outside the adapters layer should pass ``build_production``.

    Returns:
        Exit code reported by the CLI run.

    Raises:
        ValueError: If services_factory is not provided.

    Example:
        >>> from agentdag.composition import build_production
        >>> exit_code = main(["--help"], services_factory=build_production)  # doctest: +SKIP
        >>> exit_code == 0  # doctest: +SKIP
        True
    """
    if services_factory is None:
        raise ValueError("services_factory is required. Pass build_production from composition layer.")

    previous_state = snapshot_traceback_state()
    try:
        return _run_cli(argv, services_factory=services_factory)
    finally:
        if restore_traceback:
            restore_traceback_state(previous_state)
        # Only shutdown logging from main thread to avoid killing logging for other threads.
        is_main_thread = threading.current_thread() is threading.main_thread()
        if is_main_thread and lib_log_rich.runtime.is_initialised():
            lib_log_rich.runtime.shutdown()


__all__ = ["main"]
