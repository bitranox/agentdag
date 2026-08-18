"""``agentdag run``: start, inspect, resume and approve a kernel coordinator run.

Five verbs over one run directory (design 3.1/3.4, Task 17):

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

``agentdag run _coordinate RUN_ID --runs DIR [--reason ...]``
    Hidden: the foreground path over an EXISTING run directory, invoked as the child
    process a background :attr:`~agentdag.application.kernel.ports.Scope` starts - never
    typed by hand.

The CLI never reads a credential's own content: :func:`_resolve_credential` only checks
whether a keyfile PATH exists on disk, the token/copy itself is read later by the
executor, inside the coordinator process, at the point it actually dispatches a node. A
background launch's child process starts with :data:`_ENV_ALLOWLIST` alone, not this
process's own environment, so the operator's secrets never reach it as inherited env vars.

Contents:
    * :func:`cli_run` - the ``run`` group.
    * :func:`cli_run_start`, :func:`cli_run_status`, :func:`cli_run_records`,
      :func:`cli_run_resume`, :func:`cli_run_approve`, :func:`cli_run_coordinate` - the
      five verbs, plus the hidden relaunch entry point.
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
from typing import TYPE_CHECKING, NoReturn

import rich_click as click
from pydantic import ValidationError

from agentdag.adapters.kernel.executor_claude import CredentialCopy, OAuthTokenFile
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import current_holder
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.scope_systemd import SystemdScope
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.workflows import get_workflow
from agentdag.domain.errors import WorkflowNotFound
from agentdag.domain.journal import ResultLine
from agentdag.domain.keys import content_hash, hash8
from agentdag.domain.models import ApprovePayload, Decision, RunState, RunStatus

from .. import safe_console
from ..constants import CLICK_CONTEXT_SETTINGS
from ..context import get_cli_context
from ..exit_codes import ExitCode
from ..typed_click import argument, option

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lib_layered_config import Config
    from pydantic import BaseModel

    from agentdag.adapters.kernel.executor_claude import CredentialSource
    from agentdag.application.kernel.ports import DecisionFileRef, KernelWiring
    from agentdag.application.kernel.run import RunOutcome
    from agentdag.application.workflows import WorkflowDef

__all__ = [
    "cli_run",
    "cli_run_approve",
    "cli_run_coordinate",
    "cli_run_records",
    "cli_run_resume",
    "cli_run_start",
    "cli_run_status",
]

_DEFAULT_RUNS_DIR = "/var/lib/agentdag/runs"
_DEFAULT_PARALLEL = 2
_DEFAULT_MAX_TURNS = 25
_DEFAULT_DENY_BASH = ("git push", "gh pr", "gh release", "curl -X POST", "curl --data", "wget --post")
_RESUME_REASONS = ("decision", "crash", "restart", "manual")

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

``XDG_RUNTIME_DIR``/``DBUS_SESSION_BUS_ADDRESS`` are here for :class:`SystemdScope
<agentdag.adapters.kernel.scope_systemd.SystemdScope>` itself, not the coordinator's own
needs: ``scope.start()`` launches ``systemd-run`` with THIS env (:func:`_clean_env`), and
``systemd-run --user`` refuses outright without one of the two ("Failed to connect to
user scope bus via local transport" - measured live on ``lxc-pydev`` while building this
module: an empty env makes it fail even though ``systemd-run`` is on PATH and the user
manager is up). Neither var is a secret - a socket path and a runtime directory path,
not a credential."""

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


@click.group("run", context_settings=CLICK_CONTEXT_SETTINGS)
def cli_run() -> None:
    """Start, inspect, resume and approve a kernel coordinator run."""


@click.command("start", context_settings=CLICK_CONTEXT_SETTINGS)
@argument("workflow_name", metavar="WORKFLOW")
@option("--arg", "args_kv", multiple=True, default=(), metavar="KEY=VALUE", help="One workflow argument, repeatable.")
@_RUNS_OPTION
@option("--parallel", type=click.IntRange(min=1), default=None, help="How many map branches may run at once.")
@option(
    "--policy",
    "policy_option",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="An alternate tier policy YAML (default: the shipped table).",
)
@_FOREGROUND_OPTION
@click.pass_context
def cli_run_start(
    ctx: click.Context,
    *,
    workflow_name: str,
    args_kv: tuple[str, ...],
    runs_option: Path | None,
    parallel: int | None,
    policy_option: Path | None,
    foreground: bool,
) -> None:
    """Start a new run of WORKFLOW."""
    config = get_cli_context(ctx).config
    workflow = _lookup_workflow(workflow_name)
    args = _validated_args(workflow, _parsed_arg_pairs(args_kv))
    runs_dir = _resolve_runs_dir(config, runs_option)
    _require_writable_runs_dir(runs_dir)
    wiring, credential_desc = _build_wiring(
        ctx, runs_dir=runs_dir, policy_override=policy_option, parallel_override=parallel
    )
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
        _print_outcome(run_id, _run_foreground(ctx, run_id=run_id, runs_dir=runs_dir, resume_reason=None))
        return
    scope_desc = "systemd user scope" if isinstance(wiring.scope, SystemdScope) else "plain subprocess"
    safe_console.echo(f"scope: {scope_desc}")
    handle = wiring.scope.start(
        unit=_scope_unit(run_id),
        argv=[sys.executable, "-m", "agentdag", "run", "_coordinate", run_id, "--runs", str(runs_dir)],
        env=_clean_env(),
        cwd=run_dir.root,
    )
    safe_console.echo(f"run {run_id} started (unit {handle.unit})")


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
    if run_dir.read_state().status is RunStatus.DONE:
        _fail(f"run {run_id} is done; nothing to resume")
    _relaunch(ctx, run_dir=run_dir, runs_dir=runs_dir, reason=reason_option, foreground=foreground)


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
@click.pass_context
def cli_run_coordinate(ctx: click.Context, *, run_id: str, runs_option: Path, reason_option: str | None) -> None:
    """Hidden: drive an EXISTING run's coordinator in-process. Not for direct use."""
    _print_outcome(run_id, _run_foreground(ctx, run_id=run_id, runs_dir=runs_option, resume_reason=reason_option))


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
    return Path(str(config.get("kernel.runs_dir", default=_DEFAULT_RUNS_DIR)))


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


def _config_int(config: Config, key: str, default: int) -> int:
    """Read an integer config value, defaulting when absent."""
    return int(config.get(key, default=default))


def _config_deny_bash(config: Config) -> tuple[str, ...]:
    """Read ``[kernel] deny_bash``, defaulting to the shipped denylist.

    A TOML array (the common case) comes back as a real list; an env-var override
    (``AGENTDAG___KERNEL__DENY_BASH=git push,gh pr,gh release``, per
    ``defaultconfig.d/60-kernel.toml``'s own documented convention) comes back as ONE
    comma-joined string - measured directly against ``get_config()``:
    ``lib_layered_config`` does not split it, so this does, matching the documented
    format rather than treating the whole string as a single denylist pattern.
    """
    raw = config.get("kernel.deny_bash", default=list(_DEFAULT_DENY_BASH))
    if isinstance(raw, str):
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    return tuple(str(item) for item in raw)


def _build_wiring(
    ctx: click.Context, *, runs_dir: Path, policy_override: Path | None, parallel_override: int | None
) -> tuple[KernelWiring, str]:
    """Resolve config/CLI overrides into a fresh :class:`KernelWiring`, and the chosen credential's description."""
    config = get_cli_context(ctx).config
    services = get_cli_context(ctx).services
    credential, credential_desc = _resolve_credential(config)
    policy_path = policy_override if policy_override is not None else _shipped_policy_path()
    default_parallel = _config_int(config, "kernel.parallel", _DEFAULT_PARALLEL)
    parallel = parallel_override if parallel_override is not None else default_parallel
    wiring = services.wire_kernel(
        runs=runs_dir,
        policy_path=policy_path,
        credential=credential,
        parallel=parallel,
        max_turns=_config_int(config, "kernel.max_turns", _DEFAULT_MAX_TURNS),
        deny_bash=_config_deny_bash(config),
    )
    return wiring, credential_desc


def _clean_env() -> dict[str, str]:
    """Build a background coordinator's environment: :data:`_ENV_ALLOWLIST` only."""
    return {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}


def _scope_unit(run_id: str) -> str:
    """Return the scope unit name a background launch uses for ``run_id``.

    A HYPHEN, never ``@``: measured live on ``lxc-pydev`` while building this module,
    ``systemd-run --user --scope --unit=agentdag-run@<run_id>`` fails outright
    ("Failed to start transient scope unit: Invalid argument") because systemd reads
    ``@`` in a unit name as the template-instance separator (``name@instance.type``,
    as in ``getty@tty1.service``) - a transient ``--unit=`` name is not a template
    instantiation, so the literal ``@`` is rejected. A minimal A/B on this same host
    confirmed the character is the whole difference: ``--unit=agentdag-run@x.scope``
    fails, ``--unit=agentdag-run-x.scope`` (hyphen) succeeds, argv otherwise identical.
    """
    return f"agentdag-run-{run_id}"


def _open_run_dir(runs_dir: Path, run_id: str) -> FsRunDir:
    """Open an existing run directory, or exit naming it."""
    try:
        return FsRunDir.open(runs_dir, run_id)
    except FileNotFoundError as exc:
        _fail(str(exc))


def _run_foreground(ctx: click.Context, *, run_id: str, runs_dir: Path, resume_reason: str | None) -> RunOutcome:
    """Read RUN_ID's workflow and args off its own state, then drive the coordinator to an exit.

    Always reads ``workflow``/``args`` from ``state.json`` rather than taking them as
    parameters: every caller (``start --foreground``, ``resume``, ``approve``, and the
    hidden ``_coordinate``) reaches this only AFTER ``state.json`` already exists - a
    fresh ``start`` pre-writes it before deciding foreground vs. background - so a
    background relaunch (a fresh OS process with none of this session's parsed objects)
    bootstraps from the same place every other caller does.
    """
    run_dir = FsRunDir.open(runs_dir, run_id)
    state = run_dir.read_state()
    workflow = get_workflow(state.workflow)
    args = workflow.args_model.model_validate(state.args)
    wiring, _credential_desc = _build_wiring(ctx, runs_dir=runs_dir, policy_override=None, parallel_override=None)
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
                parallel=wiring.parallel,
                by=getpass.getuser(),
                token_id="local",  # nosec B106  # noqa: S106 - a token IDENTITY, not a secret
                resume_reason=resume_reason,
            )
        )
    except Exception as exc:
        safe_console.echo(f"run {run_id} failed: {exc}")
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
    """Relaunch ``run_dir``'s coordinator, in-process or under a fresh background scope."""
    run_id = run_dir.root.name
    if foreground:
        _print_outcome(run_id, _run_foreground(ctx, run_id=run_id, runs_dir=runs_dir, resume_reason=reason))
        return
    wiring, _credential_desc = _build_wiring(ctx, runs_dir=runs_dir, policy_override=None, parallel_override=None)
    argv = [sys.executable, "-m", "agentdag", "run", "_coordinate", run_id, "--runs", str(runs_dir), "--reason", reason]
    handle = wiring.scope.start(
        unit=_scope_unit(run_id),
        argv=argv,
        env=_clean_env(),
        cwd=run_dir.root,
    )
    safe_console.echo(f"run {run_id} started (unit {handle.unit})")


def _decision_for(
    run_dir: FsRunDir, state: RunState, *, run_id: str, node_id: str, decision_id: str, reason_text: str
) -> Decision:
    """Build the :class:`Decision` to record for ``node_id``, from the live suspend or a folded record.

    When the run is CURRENTLY suspended on exactly this node, the payload it is waiting
    on comes straight from ``state.json``'s cursor - the primary path. Otherwise (the run
    has already moved on, e.g. a repeat ``approve`` after it finished) this falls back to
    the node's own recorded decision, if exactly one exists, so a REPLAY of an
    already-answered node still resolves to the SAME (node id, payload hash) pair and
    :meth:`~agentdag.adapters.kernel.run_store_fs.FsRunDir.write_decision` reports
    "already decided" rather than a bare "not suspended".
    """
    by = getpass.getuser()
    if state.status is RunStatus.SUSPENDED and state.cursor == node_id and state.cursor_payload_hash is not None:
        payload_hash = state.cursor_payload_hash
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
            by=by,
            token_id="local",  # nosec B106  # noqa: S106 - a token IDENTITY, not a secret
            payload_hash=payload_hash,
        )
    return _decision_from_recorded(
        run_dir, state, run_id=run_id, node_id=node_id, decision_id=decision_id, reason_text=reason_text, by=by
    )


def _decision_from_recorded(
    run_dir: FsRunDir, state: RunState, *, run_id: str, node_id: str, decision_id: str, reason_text: str, by: str
) -> Decision:
    """The fallback half of :func:`_decision_for`: answer against the node's already-recorded payload."""
    existing: list[DecisionFileRef] = [ref for ref in run_dir.decision_files() if ref.node_id == node_id]
    if not existing:
        waiting = state.cursor or "nothing"
        _fail(f"run {run_id} is not waiting on {node_id!r} (status={state.status.value}, waiting on {waiting!r})")
    if len(existing) > 1:
        _fail(
            f"node {node_id!r} has {len(existing)} recorded decisions; the run must be suspended on the one to answer"
        )
    recorded = run_dir.read_decision_file(existing[0])
    return Decision(
        node_id=node_id,
        decision=decision_id,
        reason=reason_text,
        by=by,
        token_id="local",  # nosec B106  # noqa: S106 - a token IDENTITY, not a secret
        payload_hash=recorded.payload_hash,
    )


cli_run.add_command(cli_run_start)
cli_run.add_command(cli_run_status)
cli_run.add_command(cli_run_records)
cli_run.add_command(cli_run_resume)
cli_run.add_command(cli_run_approve)
cli_run.add_command(cli_run_coordinate)
