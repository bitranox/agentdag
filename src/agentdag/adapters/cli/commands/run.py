"""``agentdag run``: start, inspect, resume, approve and cancel a kernel coordinator run.

Six verbs over one run directory (design 3.1/3.4, Task 17; ``cancel`` added M3):

``agentdag run start WORKFLOW [--arg key=value]... [--runs DIR] [--parallel N] [--policy FILE] [--foreground]``
    Validate WORKFLOW's arguments, mint a run id, create the run directory, and either
    drive the coordinator in-process (``--foreground``, the testable path) or launch it
    detached under a :class:`~agentdag.application.kernel.ports.Scope`.

``agentdag run status RUN_ID [--runs DIR]``
    Print ``state.json``'s fields and the journal's last event.

``agentdag run records RUN_ID [--runs DIR] [--json]``
    Print one line per result the journal holds (or the same as JSON).

``agentdag run resume RUN_ID [--runs DIR] [--foreground] [--reason ...]``
    Relaunch a run that is not ``done``.

``agentdag run approve RUN_ID NODE_ID --decision ID [--reason TEXT] [--runs DIR] [--no-relaunch] [--foreground]``
    Record a human decision for a suspended approve node, then relaunch unless
    ``--no-relaunch``.

``agentdag run cancel RUN_ID [--runs DIR]``
    Write the whole-run cancel intent and return AT ONCE with ``cancelling`` (mcp-surface
    O25); then, on the SAME invocation, attempt the actual scope kill and the VERIFIED
    journal outcome (design 3.4) - a live coordinator still holding the run's lock defers
    that half to a later ``run cancel`` retry, or the startup sweep on the run's next
    relaunch attempt.

``agentdag run _coordinate RUN_ID --runs DIR [--reason ...]``
    Hidden: the foreground path over an EXISTING run directory, invoked as the child
    process a background :attr:`~agentdag.application.kernel.ports.Scope` starts - never
    typed by hand.

The CLI never reads a credential's own content: :func:`_resolve_credential` only checks
whether a keyfile PATH exists on disk, the token/copy itself is read later by the
executor, inside the coordinator process, at the point it actually dispatches a node. A
background launch's child process starts with :data:`_ENV_ALLOWLIST` alone, not this
process's own environment, so the operator's secrets never reach it as inherited env vars.

``--parallel``/``--policy`` (``run start``'s own options, or config ``kernel.parallel``)
and ``kernel.max_turns``/``kernel.deny_bash`` are COORDINATOR-level: they apply
uniformly to whichever workflow a run drives, resolved once by :func:`_build_wiring` and
never read from a workflow's own typed args (a workflow's ``args_model``, e.g. graph A's
``GraphAArgs``, carries only what the WORKFLOW itself needs - ``repos_file``,
``brief_file``, ``scratch``, ``model`` - not a scheduling knob the coordinator already
owns). A background relaunch's ``_coordinate`` re-derives them from config UNLESS
``run start`` forwards its own ``--parallel``/``--policy`` into ``_coordinate``'s argv,
which it does exactly when they were given (see :func:`_launch_background`).

Contents:
    * :func:`cli_run` - the ``run`` group.
    * :func:`cli_run_start`, :func:`cli_run_status`, :func:`cli_run_records`,
      :func:`cli_run_resume`, :func:`cli_run_approve`, :func:`cli_run_cancel`,
      :func:`cli_run_coordinate` - the six verbs, plus the hidden relaunch entry point.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

import rich_click as click
import tomllib
from pydantic import ValidationError

from agentdag.adapters.kernel.executor_claude import CredentialCopy, OAuthTokenFile
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import current_holder
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.scope_systemd import SystemdScope
from agentdag.application.kernel.cancel import request_cancel, resolve_cancel, scope_unit, sweep_stale_scope
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.workflows import get_workflow
from agentdag.domain.journal import ResultLine
from agentdag.domain.kernel_errors import RunRefused, WorkflowNotFound
from agentdag.domain.keys import content_hash, hash8
from agentdag.domain.models import ApprovePayload, Decision, RunState, RunStatus
from agentdag.domain.scrub import scrub

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..context import get_cli_context
from ..exit_codes import ExitCode
from ..typed_click import argument, option

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lib_layered_config import Config
    from pydantic import BaseModel

    from agentdag.adapters.kernel.executor_claude import CredentialSource
    from agentdag.application.kernel.ports import KernelWiring, Scope
    from agentdag.application.kernel.run import RunOutcome
    from agentdag.application.workflows import WorkflowDef

__all__ = [
    "cli_run",
    "cli_run_approve",
    "cli_run_cancel",
    "cli_run_coordinate",
    "cli_run_records",
    "cli_run_resume",
    "cli_run_start",
    "cli_run_status",
]

_RESUME_REASONS = ("decision", "crash", "restart", "manual")

_LAUNCH_CONFIRM_TIMEOUT_S = 2.0
"""How long :func:`_launch_background` waits for a background launch to prove itself."""

_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "SYSTEMROOT",
    "USERPROFILE",
    "COMSPEC",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)
"""The env a background coordinator process starts with - never the operator's own
environment: a launch by an interactive shell must not hand a spawned run its secrets.

``HOME`` is the OPERATOR's home directory here ON PURPOSE, not a scrubbed placeholder:
the COORDINATOR process itself (never a dispatched Claude node) reads it to resolve
:func:`_resolve_credential`'s copy-source path (``~/.claude/.credentials.json``) and its
own config search path (``~/.config/agentdag``). A dispatched node's own env does NOT
inherit this ``HOME`` - see the next paragraph.

``XDG_RUNTIME_DIR``/``DBUS_SESSION_BUS_ADDRESS`` stay in this list, and both reasons are
about a ``systemd --user`` client rather than about the coordinator's own bookkeeping.
First, this env is not only the child's: ``scope.start()`` hands it to
:class:`SystemdScope <agentdag.adapters.kernel.scope_systemd.SystemdScope>`, which passes
it as the environment of the ``systemd-run`` process ITSELF, and ``systemd-run --user``
refuses outright without one of the two ("Failed to connect to user scope bus via local
transport" - measured live on ``lxc-pydev`` while building this module: an empty env makes
it fail even though ``systemd-run`` is on PATH and the user manager is up). Second, the
coordinator process launched inside the scope re-enters
:func:`~agentdag.composition.kernel.wire_kernel` to build its own wiring, and that calls
``_choose_scope()``, which probes ``systemctl --user is-system-running`` - a user-bus
client too, so without the two vars that probe answers from a session it cannot reach.
Neither var is a secret: a socket path and a runtime directory path, not a credential.

Neither reaches a dispatched Claude node, whose env
:mod:`agentdag.adapters.kernel.executor_claude` builds from its own, entirely separate
``_ALLOWLIST_KEYS`` (neither var, nor this module's ``HOME``, is in it), and neither
reaches a ``make test`` GATE subprocess either: that runs through
:class:`~agentdag.adapters.graph_a.gate_make.MakeTestGate`, which passes its own explicit
:data:`~agentdag.adapters.graph_a.gate_make.GATE_ENV_ALLOWLIST` env and deliberately
leaves both out, so a ``systemd-run --user --scope`` started from inside the gate cannot
create a unit that would outlive this run's own scope."""

_RUNS_OPTION = option(
    "--runs",
    "runs_option",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory holding every run (default: config kernel.runs_dir).",
)
_FOREGROUND_OPTION = option(
    "--foreground",
    is_flag=True,
    default=False,
    help="Run the coordinator in-process instead of a background scope.",
)
_PARALLEL_OPTION = option(
    "--parallel",
    "parallel_option",
    type=click.IntRange(min=1),
    default=None,
    help="How many map branches may run at once (default: config kernel.parallel).",
)
_POLICY_OPTION = option(
    "--policy",
    "policy_option",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="An alternate tier policy YAML (default: the shipped table).",
)
"""Shared with ``_coordinate`` (:func:`cli_run_coordinate`), which is exactly HOW a
background relaunch keeps these two coordinator-level knobs: :func:`cli_run_start`
forwards its own values into ``_coordinate``'s argv when they were given
(:func:`_launch_background`'s caller), rather than the relaunch silently re-deriving
config-only defaults that could differ from what the operator actually asked for."""


@click.group("run", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_run() -> None:
    """Start, inspect, resume and approve a kernel coordinator run."""


@click.command("start", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("workflow_name", metavar="WORKFLOW")
@option("--arg", "args_kv", multiple=True, default=(), metavar="KEY=VALUE", help="One workflow argument, repeatable.")
@_RUNS_OPTION
@_PARALLEL_OPTION
@_POLICY_OPTION
@_FOREGROUND_OPTION
@click.pass_context
def cli_run_start(
    ctx: click.Context,
    *,
    workflow_name: str,
    args_kv: tuple[str, ...],
    runs_option: Path | None,
    parallel_option: int | None,
    policy_option: Path | None,
    foreground: bool,
) -> None:
    """Start a new run of WORKFLOW."""
    config = get_cli_context(ctx).config
    workflow = _lookup_workflow(workflow_name)
    args = _validated_args(workflow, _parsed_arg_pairs(args_kv))
    runs_dir = _resolve_runs_dir(config, runs_option)
    _require_writable_runs_dir(runs_dir)
    wiring, credential_desc = _build_wiring(ctx, policy_override=policy_option, parallel_override=parallel_option)
    safe_console.echo(f"credential: {credential_desc}")
    run_id = _mint_run_id()
    run_dir = FsRunDir.create(runs_dir, run_id)
    run_dir.write_state(
        RunState(
            run_id=run_id,
            workflow=workflow.name,
            args=args.model_dump(mode="json"),
            owner=getpass.getuser(),
            status=RunStatus.RUNNING,
            policy_version=wiring.policy.version,
        )
    )
    if foreground:
        outcome = _run_foreground(
            ctx,
            run_id=run_id,
            runs_dir=runs_dir,
            resume_reason=None,
            policy_override=policy_option,
            parallel_override=parallel_option,
        )
        _print_outcome(run_id, outcome)
        return
    scope_desc = "systemd user scope" if isinstance(wiring.scope, SystemdScope) else "plain subprocess"
    safe_console.echo(f"scope: {scope_desc}")
    argv = [sys.executable, "-m", "agentdag", "run", "_coordinate", run_id, "--runs", str(runs_dir)]
    if parallel_option is not None:
        argv += ["--parallel", str(parallel_option)]
    if policy_option is not None:
        argv += ["--policy", str(policy_option)]
    _launch_background(
        wiring.scope, unit=scope_unit(run_id), argv=argv, env=_clean_env(), cwd=run_dir.root, run_id=run_id
    )


@click.command("status", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("run_id")
@_RUNS_OPTION
@click.pass_context
def cli_run_status(ctx: click.Context, *, run_id: str, runs_option: Path | None) -> None:
    """Print RUN_ID's current state and its last journal event."""
    config = get_cli_context(ctx).config
    run_dir = _open_run_dir(_resolve_runs_dir(config, runs_option), run_id)
    state = run_dir.read_state()
    lines = JsonlJournal(run_dir.journal_path, run_dir.audit_path).lines()
    safe_console.echo(f"run: {run_id}")
    safe_console.echo(f"workflow: {state.workflow}")
    safe_console.echo(f"status: {state.status.value}")
    safe_console.echo(f"cursor: {state.cursor}")
    safe_console.echo(f"cursor_payload_hash: {state.cursor_payload_hash}")
    safe_console.echo(f"tokens_by_row: {state.tokens_by_row}")
    safe_console.echo(f"last event: {lines[-1].event} at {lines[-1].at}" if lines else "last event: (none)")


@click.command("records", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("run_id")
@_RUNS_OPTION
@option("--json", "as_json", is_flag=True, default=False, help="Print the records as a JSON array.")
@click.pass_context
def cli_run_records(ctx: click.Context, *, run_id: str, runs_option: Path | None, as_json: bool) -> None:
    """Print RUN_ID's result records, one per dispatched node."""
    config = get_cli_context(ctx).config
    run_dir = _open_run_dir(_resolve_runs_dir(config, runs_option), run_id)
    lines = JsonlJournal(run_dir.journal_path, run_dir.audit_path).lines()
    records = [line.record for line in lines if isinstance(line, ResultLine)]
    if as_json:
        safe_console.echo(json.dumps([record.model_dump(mode="json", by_alias=True) for record in records]))
        return
    for record in records:
        charged = sum(record.charged_tokens.values())
        safe_console.echo(f"{record.node_id:<24} {record.attempt:<4} {record.status.value:<10} {charged}")


@click.command("resume", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("run_id")
@_RUNS_OPTION
@_FOREGROUND_OPTION
@option(
    "--reason",
    "reason_option",
    type=click.Choice(_RESUME_REASONS),
    default="manual",
    show_default=True,
    help="Why this relaunch is happening.",
)
@click.pass_context
def cli_run_resume(
    ctx: click.Context, *, run_id: str, runs_option: Path | None, foreground: bool, reason_option: str
) -> None:
    """Relaunch a coordinator for RUN_ID."""
    config = get_cli_context(ctx).config
    runs_dir = _resolve_runs_dir(config, runs_option)
    run_dir = _open_run_dir(runs_dir, run_id)
    status = run_dir.read_state().status
    if status is RunStatus.DONE:
        _fail(f"run {run_id} is done; nothing to resume")
    if status is RunStatus.CANCELLED:
        _fail(f"run {run_id} is cancelled; nothing to resume")
    if status is RunStatus.CANCELLING:
        _fail(f"run {run_id} is cancelling; run 'agentdag run cancel {run_id}' again to retry verification")
    _relaunch(ctx, run_dir=run_dir, runs_dir=runs_dir, reason=reason_option, foreground=foreground)


@click.command("cancel", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("run_id")
@_RUNS_OPTION
@click.pass_context
def cli_run_cancel(ctx: click.Context, *, run_id: str, runs_option: Path | None) -> None:
    """Cancel RUN_ID: write the intent, stop its scope, and record the verified outcome.

    Prints ``cancelling`` at once (mcp-surface O25: this never waits for the run's own
    deadline), then attempts the actual kill and verification in the same invocation -
    the ONE thing this cannot do synchronously is wait out ANOTHER, still-live
    coordinator's own lock; that half is reported as unverified and left to a later
    ``run cancel`` retry, or the startup sweep the next time this run is relaunched.
    """
    config = get_cli_context(ctx).config
    runs_dir = _resolve_runs_dir(config, runs_option)
    run_dir = _open_run_dir(runs_dir, run_id)
    try:
        requested = request_cancel(
            run_dir,
            by=getpass.getuser(),
            token_id="local",  # nosec B106  # noqa: S106 - a token IDENTITY, not a secret
        )
    except RunRefused as exc:
        _fail(str(exc))
    if requested.status is RunStatus.CANCELLED:
        safe_console.echo(f"run {run_id} cancelled (verified: true)")
        return
    safe_console.echo(f"run {run_id} cancelling")
    wiring, _credential_desc = _build_wiring(ctx, policy_override=None, parallel_override=None)
    journal = wiring.journal_factory(run_dir.journal_path, run_dir.audit_path)
    resolved = resolve_cancel(
        run_dir, journal, scope=wiring.scope, lock=wiring.lock, clock=wiring.clock, holder=current_holder()
    )
    suffix = f" ({resolved.reason})" if resolved.reason else ""
    safe_console.echo(f"run {run_id} cancel verified: {'true' if resolved.verified else 'false'}{suffix}")


@click.command("approve", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("run_id")
@argument("node_id")
@option("--decision", "decision_id", required=True, help="The option id to record.")
@option("--reason", "reason_text", default="", help="A free-text reason, recorded alongside the decision.")
@_RUNS_OPTION
@option("--no-relaunch", is_flag=True, default=False, help="Record the decision but do not relaunch the coordinator.")
@_FOREGROUND_OPTION
@click.pass_context
def cli_run_approve(
    ctx: click.Context,
    *,
    run_id: str,
    node_id: str,
    decision_id: str,
    reason_text: str,
    runs_option: Path | None,
    no_relaunch: bool,
    foreground: bool,
) -> None:
    """Record a decision for RUN_ID's NODE_ID, and relaunch unless --no-relaunch."""
    config = get_cli_context(ctx).config
    runs_dir = _resolve_runs_dir(config, runs_option)
    run_dir = _open_run_dir(runs_dir, run_id)
    state = run_dir.read_state()
    decision = _decision_for(
        run_dir, state, run_id=run_id, node_id=node_id, decision_id=decision_id, reason_text=reason_text
    )
    try:
        run_dir.write_decision(decision)
    except FileExistsError:
        _fail(f"run {run_id} already decided for this payload at {node_id!r}")
    safe_console.echo(f"run {run_id} recorded decision {decision.decision!r} for {node_id!r}")
    if no_relaunch:
        return
    _relaunch(ctx, run_dir=run_dir, runs_dir=runs_dir, reason="decision", foreground=foreground)


@click.command("_coordinate", context_settings=CLICK_CONTEXT_SETTINGS, hidden=True)
@argument("run_id")
@option("--runs", "runs_option", type=click.Path(file_okay=False, path_type=Path), required=True)
@option("--reason", "reason_option", type=click.Choice(_RESUME_REASONS), default=None)
@_PARALLEL_OPTION
@_POLICY_OPTION
@click.pass_context
def cli_run_coordinate(
    ctx: click.Context,
    *,
    run_id: str,
    runs_option: Path,
    reason_option: str | None,
    parallel_option: int | None,
    policy_option: Path | None,
) -> None:
    """Hidden: drive an EXISTING run's coordinator in-process. Not for direct use.

    ``--parallel``/``--policy`` are what ``run start``'s own background launch forwards
    here when it was given them (see :data:`_PARALLEL_OPTION`/:data:`_POLICY_OPTION`'s
    shared docstring); absent, both fall back to config, exactly as every other
    relaunch path (``resume``, ``approve``) already does.
    """
    outcome = _run_foreground(
        ctx,
        run_id=run_id,
        runs_dir=runs_option,
        resume_reason=reason_option,
        policy_override=policy_option,
        parallel_override=parallel_option,
    )
    _print_outcome(run_id, outcome)


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------


def _fail(message: str) -> NoReturn:
    """Print ``message`` and exit with :attr:`ExitCode.INVALID_ARGUMENT`."""
    safe_console.echo(message)
    raise SystemExit(ExitCode.INVALID_ARGUMENT)


def _lookup_workflow(name: str) -> WorkflowDef:
    """Return the workflow called ``name``, or exit naming the ones there are."""
    try:
        return get_workflow(name)
    except WorkflowNotFound as exc:
        _fail(str(exc))


def _parsed_arg_pairs(pairs: Sequence[str]) -> dict[str, str]:
    """Split each ``KEY=VALUE`` string, or exit naming the malformed one."""
    result: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            _fail(f"--arg must be KEY=VALUE, got {pair!r}")
        result[key] = value
    return result


def _validated_args(workflow: WorkflowDef, kwargs: dict[str, str]) -> BaseModel:
    """Parse ``kwargs`` through ``workflow``'s own args model, or exit with pydantic's message."""
    try:
        return workflow.args_model.model_validate(kwargs)
    except ValidationError as exc:
        _fail(str(exc))


def _resolve_runs_dir(config: Config, override: Path | None) -> Path:
    """Return ``override``, or ``[kernel] runs_dir`` from config."""
    if override is not None:
        return override
    return Path(str(config.get("kernel.runs_dir", default=_packaged_kernel_defaults()["runs_dir"])))


def _require_writable_runs_dir(path: Path) -> None:
    """Exit naming ``path`` when it does not exist or is not writable; never creates it."""
    if not path.is_dir() or not os.access(path, os.W_OK):
        _fail(f"--runs {path} does not exist or is not writable")


def _mint_run_id() -> str:
    """Mint a run id: a UTC timestamp plus 6 random hex characters.

    The CLI is the scheduler - allowed to read the clock and randomness here; the
    coordinator itself never may (design 3.3, ``assert_deterministic``).
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{os.urandom(3).hex()}"


def _validate_run_id(run_id: str) -> None:
    """Refuse a RUN_ID that could escape ``runs_dir`` or corrupt a scope unit name.

    The SAME character rule :meth:`~agentdag.adapters.kernel.run_store_fs.FsRunDir.
    _validate_node_id` applies to a node id: no path separator, no ``..`` - so a value
    taken straight from argv can never be joined onto ``runs_dir`` (``FsRunDir.open``
    does not itself check this) or folded into ``scope_unit`` and reach outside
    either. Argv is untrusted input at this CLI's boundary; this runs BEFORE any path
    join or unit-name build, never after.

    Raises:
        SystemExit: via :func:`_fail` - ``run_id`` is empty, or contains ``/``, ``\\``
            or ``..``.
    """
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        _fail(f"{run_id!r} is not a valid run id")


def _resolve_credential(config: Config) -> tuple[CredentialSource, str]:
    """Pick the OAuth-token keyfile named in config if it exists, else a credential copy.

    Only checks whether the keyfile PATH exists - never reads its content, which the
    executor reads fresh inside the coordinator process, at each node's dispatch.
    """
    token_file = str(config.get("credentials.claude_oauth_token_file", default=""))
    if token_file and Path(token_file).is_file():
        return OAuthTokenFile(path=Path(token_file)), f"OAuth token keyfile at {token_file}"
    home_copy = Path.home() / ".claude" / ".credentials.json"
    return CredentialCopy(source_path=home_copy), f"a private copy of {home_copy}"


def _shipped_policy_path() -> Path:
    """Return the tier policy YAML shipped with this package."""
    return Path(str(files("agentdag.policy") / "tier-policy.yaml"))


def _packaged_kernel_defaults() -> dict[str, object]:
    """Read the packaged ``60-kernel.toml``'s own ``[kernel]`` table.

    The single source every config-less fallback below reads FROM, rather than a
    second, hand-typed Python literal that could silently drift from the shipped
    config: ``config.get(key, default=...)`` needs SOME value to fall back to when
    ``get_config()`` genuinely carries nothing for that key, and this is where it
    comes from - never a number re-declared independently in this module.
    """
    path = Path(str(files("agentdag.adapters.config") / "defaultconfig.d" / "60-kernel.toml"))
    with path.open("rb") as handle:
        table = tomllib.load(handle)
    return table["kernel"]


def _config_int(config: Config, key: str, default_key: str) -> int:
    """Read an integer config value, defaulting to the packaged config's own ``default_key``."""
    return int(config.get(key, default=_packaged_kernel_defaults()[default_key]))


def _config_deny_bash(config: Config) -> tuple[str, ...]:
    """Read ``[kernel] deny_bash``, defaulting to the packaged denylist.

    A TOML array (the common case) comes back as a real list; an env-var override
    (``AGENTDAG___KERNEL__DENY_BASH=git push,gh pr,gh release``, per
    ``defaultconfig.d/60-kernel.toml``'s own documented convention) comes back as ONE
    comma-joined string - measured directly against ``get_config()``:
    ``lib_layered_config`` does not split it, so this does, matching the documented
    format rather than treating the whole string as a single denylist pattern.
    """
    raw = config.get("kernel.deny_bash", default=_packaged_kernel_defaults()["deny_bash"])
    if isinstance(raw, str):
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    return tuple(str(item) for item in raw)


def _build_wiring(
    ctx: click.Context, *, policy_override: Path | None, parallel_override: int | None
) -> tuple[KernelWiring, str]:
    """Resolve config/CLI overrides into a fresh :class:`KernelWiring`, and the chosen credential's description."""
    config = get_cli_context(ctx).config
    services = get_cli_context(ctx).services
    credential, credential_desc = _resolve_credential(config)
    policy_path = policy_override if policy_override is not None else _shipped_policy_path()
    default_parallel = _config_int(config, "kernel.parallel", "parallel")
    parallel = parallel_override if parallel_override is not None else default_parallel
    wiring = services.wire_kernel(
        policy_path=policy_path,
        credential=credential,
        parallel=parallel,
        max_turns=_config_int(config, "kernel.max_turns", "max_turns"),
        deny_bash=_config_deny_bash(config),
    )
    return wiring, credential_desc


def _clean_env() -> dict[str, str]:
    """Build a background coordinator's environment: :data:`_ENV_ALLOWLIST` only."""
    return {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}


def _open_run_dir(runs_dir: Path, run_id: str) -> FsRunDir:
    """Open an existing run directory, or exit naming it.

    Validates ``run_id`` FIRST (:func:`_validate_run_id`): every command that opens a
    run by an id taken from argv (``status``, ``records``, ``resume``, ``approve``, and
    - through :func:`_run_foreground`, which reuses this rather than calling
    ``FsRunDir.open`` a second way - the hidden ``_coordinate``) funnels through here,
    so this is the ONE place that check has to happen.
    """
    _validate_run_id(run_id)
    try:
        return FsRunDir.open(runs_dir, run_id)
    except FileNotFoundError as exc:
        _fail(str(exc))


def _launch_background(
    scope: Scope, *, unit: str, argv: Sequence[str], env: Mapping[str, str], cwd: Path, run_id: str
) -> None:
    """Start ``argv`` under ``scope``, confirm it launched, then report - or fail loudly.

    :meth:`~agentdag.application.kernel.ports.Scope.start` only Popens the launcher and
    returns; without calling :meth:`~agentdag.application.kernel.ports.Scope.confirm`
    straight after, a launcher that failed immediately (a bad unit name, a missing
    ``systemd-run``) would still be reported ``started`` and exit 0. This is the ONE
    place ``run start`` and a background relaunch (:func:`_relaunch`) launch a scope, so
    it is the ONE place that confirmation has to happen.

    Raises:
        SystemExit: :attr:`~agentdag.adapters.cli.exit_codes.ExitCode.GENERAL_ERROR` -
            the launch did not prove itself within :data:`_LAUNCH_CONFIRM_TIMEOUT_S`;
            the message carries whatever :attr:`~agentdag.application.kernel.ports.
            LaunchResult.stderr` captured.
    """
    handle = scope.start(unit=unit, argv=argv, env=env, cwd=cwd)
    result = scope.confirm(handle, timeout_s=_LAUNCH_CONFIRM_TIMEOUT_S)
    if not result.alive:
        safe_console.echo(f"run {run_id} failed to start (unit {handle.unit}): {result.stderr}")
        raise SystemExit(ExitCode.GENERAL_ERROR)
    safe_console.echo(f"run {run_id} started (unit {handle.unit}, log {handle.log_path})")


def _run_foreground(
    ctx: click.Context,
    *,
    run_id: str,
    runs_dir: Path,
    resume_reason: str | None,
    policy_override: Path | None = None,
    parallel_override: int | None = None,
) -> RunOutcome:
    """Read RUN_ID's workflow and args off its own state, then drive the coordinator to an exit.

    Always reads ``workflow``/``args`` from ``state.json`` rather than taking them as
    parameters: every caller (``start --foreground``, ``resume``, ``approve``, and the
    hidden ``_coordinate``) reaches this only AFTER ``state.json`` already exists - a
    fresh ``start`` pre-writes it before deciding foreground vs. background - so a
    background relaunch (a fresh OS process with none of this session's parsed objects)
    bootstraps from the same place every other caller does.

    ``policy_override``/``parallel_override`` default to ``None`` (config only) for
    ``resume``/``approve``, which carry no such CLI options of their own; ``start
    --foreground`` and ``_coordinate`` pass their OWN ``--parallel``/``--policy``
    through, so this ends up building the SAME wiring :func:`cli_run_start` already
    built once for the state pre-write, rather than silently re-deriving config-only
    defaults that could disagree with what the operator actually asked for.
    """
    run_dir = _open_run_dir(runs_dir, run_id)
    state = run_dir.read_state()
    workflow = get_workflow(state.workflow)
    args = workflow.args_model.model_validate(state.args)
    wiring, _credential_desc = _build_wiring(ctx, policy_override=policy_override, parallel_override=parallel_override)
    # The startup sweep (M3): a fresh run's own unit was never started under any scope
    # kind, so this is always a safe no-op for `start --foreground`; for a relaunch of an
    # EXISTING run (resume, approve, or this in-process path of _coordinate itself) it
    # stops a scope a dead coordinator left draining before this launch dispatches
    # anything into the same worktrees, gate lock or credential store.
    sweep_stale_scope(run_dir, scope=wiring.scope)
    journal = wiring.journal_factory(run_dir.journal_path, run_dir.audit_path)
    try:
        return asyncio.run(
            run_coordinator(
                run_dir=run_dir,
                journal=journal,
                clock=wiring.clock,
                lock=wiring.lock,
                holder=current_holder(),
                workflow=workflow,
                args=args,
                executors=wiring.executors,
                gate_port=wiring.gate_port,
                git=wiring.git,
                scanner=wiring.scanner,
                policy=wiring.policy,
                sandbox=wiring.sandbox,
                parallel=wiring.parallel,
                by=getpass.getuser(),
                token_id="local",  # nosec B106  # noqa: S106 - a token IDENTITY, not a secret
                resume_reason=resume_reason,
            )
        )
    except Exception as exc:
        # The one exception-to-output sink outside the kernel: an exception's text can carry a
        # secret-shaped string it echoed back (a header, a URL with a token in it), exactly as a
        # node body's can, and the dispatcher and the executor already scrub theirs before
        # writing. The console is a sink like any file, so it gets the same guarantee.
        message = cast("str", scrub(f"{exc}"))
        safe_console.echo(f"run {run_id} failed: {message}")
        raise SystemExit(ExitCode.GENERAL_ERROR) from exc


def _print_outcome(run_id: str, outcome: RunOutcome) -> None:
    """Print one launch's terminal status, matching what the RED tests match on."""
    if outcome.status is RunStatus.SUSPENDED:
        safe_console.echo(f"run {run_id} suspended at {outcome.suspended_node}")
    elif outcome.status is RunStatus.DONE:
        safe_console.echo(f"run {run_id} done")
    else:
        safe_console.echo(f"run {run_id} {outcome.status.value}")


def _relaunch(ctx: click.Context, *, run_dir: FsRunDir, runs_dir: Path, reason: str, foreground: bool) -> None:
    """Relaunch ``run_dir``'s coordinator, in-process or under a fresh background scope.

    ``resume``/``approve`` carry no ``--parallel``/``--policy`` of their own, so this
    relaunch's ``_coordinate`` argv omits both and falls back to config, same as it
    always has - only ``run start``'s OWN first background launch (:func:`cli_run_start`)
    forwards them, since only it was actually given any to forward.
    """
    run_id = run_dir.root.name
    if foreground:
        _print_outcome(run_id, _run_foreground(ctx, run_id=run_id, runs_dir=runs_dir, resume_reason=reason))
        return
    wiring, _credential_desc = _build_wiring(ctx, policy_override=None, parallel_override=None)
    # A background relaunch REUSES this run's unit name (scope_unit is deterministic per
    # run_id, not per launch), so a scope a dead coordinator left draining under that SAME
    # name must be stopped BEFORE scope.start() below - systemd refuses a transient unit
    # name already in use, and a fresh coordinator must never start while an old one's
    # children can still be touching the same worktrees.
    sweep_stale_scope(run_dir, scope=wiring.scope)
    argv = [sys.executable, "-m", "agentdag", "run", "_coordinate", run_id, "--runs", str(runs_dir), "--reason", reason]
    _launch_background(
        wiring.scope, unit=scope_unit(run_id), argv=argv, env=_clean_env(), cwd=run_dir.root, run_id=run_id
    )


def _decision_for(
    run_dir: FsRunDir, state: RunState, *, run_id: str, node_id: str, decision_id: str, reason_text: str
) -> Decision:
    """Build the :class:`Decision` to record for ``node_id``, reading ``state.json``'s live cursor.

    Refuses outright rather than falling back to any OTHER node's or any PAST payload's
    already-recorded answer - an earlier version of this function had exactly that
    fallback, kept safe only by an invariant nothing enforced (the fold that clears
    ``cursor``/``cursor_payload_hash`` on any non-suspend exit); this version reads and
    refuses explicitly instead, in the order an operator would ask the questions:

    1. the run is not even suspended - nothing to decide, name its actual status;
    2. it IS suspended, but on a DIFFERENT node - name both, so the caller sees which
       node it should have asked about;
    3. it is suspended on exactly this node, but (:meth:`~agentdag.adapters.kernel.
       run_store_fs.FsRunDir.read_decision`) this EXACT payload already has an answer -
       a repeat ``approve`` for the same suspend, reported explicitly rather than only
       via :meth:`~agentdag.adapters.kernel.run_store_fs.FsRunDir.write_decision`'s
       own write-once ``FileExistsError`` (still the last-resort guard against a race
       between this check and that write, not the primary signal any more).
    """
    if state.status is not RunStatus.SUSPENDED:
        _fail(f"run {run_id} is not waiting on a decision (status={state.status.value})")
    if state.cursor != node_id:
        _fail(f"run {run_id} is suspended on {state.cursor!r}, not {node_id!r}")
    if state.cursor_payload_hash is None:
        _fail(f"run {run_id}: state.json is suspended on {node_id!r} but names no payload hash")
    payload_hash = state.cursor_payload_hash
    if run_dir.read_decision(node_id, payload_hash) is not None:
        _fail(f"run {run_id} already decided for this payload at {node_id!r}")
    rel = f"nodes/{node_id}/{hash8(payload_hash)}/payload.json"
    try:
        text = run_dir.read_text(rel)
    except FileNotFoundError:
        _fail(f"run {run_id}: the payload file for {node_id!r} is missing at {rel}")
    if content_hash(text) != payload_hash:
        _fail(f"run {run_id}: the payload on disk at {rel} does not match state.json's cursor_payload_hash")
    payload = ApprovePayload.model_validate_json(text)
    valid_ids = sorted(option_.id for option_ in payload.options)
    if decision_id not in valid_ids:
        _fail(f"{decision_id!r} is not one of {node_id!r}'s offered options: {valid_ids}")
    return Decision(
        node_id=node_id,
        decision=decision_id,
        reason=reason_text,
        by=getpass.getuser(),
        token_id="local",  # nosec B106  # noqa: S106 - a token IDENTITY, not a secret
        payload_hash=payload_hash,
    )


cli_run.add_command(cli_run_start)
cli_run.add_command(cli_run_status)
cli_run.add_command(cli_run_records)
cli_run.add_command(cli_run_resume)
cli_run.add_command(cli_run_approve)
cli_run.add_command(cli_run_cancel)
cli_run.add_command(cli_run_coordinate)
