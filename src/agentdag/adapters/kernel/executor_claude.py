"""The Claude kernel executor: allowlisted env, a per-node credential, PreToolUse
hooks, and tokens that mean what they say (design 7, M2 probe).

Each node gets its own :class:`~claude_agent_sdk.ClaudeSDKClient`, run under
``permission_mode="dontAsk"`` with the two hooks :mod:`.hooks_claude` builds -
``deny_outside_root`` matched against ``Write|Edit|MultiEdit|NotebookEdit``,
``deny_bash_commands`` matched against ``Bash`` - so nothing not pre-approved ever
prompts and nothing outside the isolation root or on the bash denylist is silently
allowed. ``setting_sources=[]`` keeps the coordinator's own project settings out of
the node's context, same as :mod:`agentdag.adapters.graph_a.work_claude_sdk` (M1); this
is new code sharing only the idea, not the module.

The M2 probe (``workflow/design/probes/m2-hooks-dontask.md`` in RESEARCH) measured
that these two hooks work exactly as intended for the tools they are matched against,
and ALSO that neither one sees a write made through ``Bash`` shell redirection instead
of the matched tool - a known, already-documented gap (``2026-08-17-agentdag-design.md``
section 7) that the isolation-root scan (Task 13) is the backstop for, not this module.

The child environment is a TRUE ALLOWLIST, never the coordinator's own: a
:class:`CredentialSource`'s :func:`child_env` returns ONLY :data:`_ALLOWLIST_KEYS`
(the ones actually set), ``HOME``, and the credential itself, and
:func:`_blank_everything_else` then forces every OTHER variable this process
inherited to ``""`` - not a guess at which names "look secret" (an earlier version
of this module blanked by regex, which missed ``SSH_AUTH_SOCK`` and
``AWS_ACCESS_KEY_ID`` outright: neither name contains token/secret/password/
authorization/credential). This is two layers, because the SDK MERGES its ``env``
argument OVER ``os.environ`` (M1 note) rather than replacing it, so omitting a key
lets the merge read it straight from the coordinator's own process; the SECOND layer
is that the coordinator process itself must be started with a clean environment
(Task 17), so a leak needs both to fail. The credential is minted per-node from one
of two sources (D3, RESEARCH): :class:`OAuthTokenFile` (the measured default: D3 arm
A2 proved ``CLAUDE_CODE_OAUTH_TOKEN`` authenticates a child whose
``CLAUDE_CONFIG_DIR`` is empty) or :class:`CredentialCopy` (D3 arm C: a private,
owner-only copy of the operator's ``.credentials.json``, mirroring M1's
``_copy_credential``).

Every streamed message is scrubbed (:func:`~agentdag.domain.scrub.scrub`, the domain
module - this adapter used to define it locally, see that module's own docstring for
why it moved) and appended to ``node_dir/transcript.jsonl`` (:func:`append_transcript`)
as it arrives, owner-only (``0600``). The redaction guarantee is exactly two
mechanisms, nothing broader: a dict VALUE is redacted when its own KEY is named like a
secret (``password``, ``token``, ...); a STRING value anywhere - regardless of its key
- is redacted when it matches one of the known secret token SHAPES (``sk-ant-...``,
``oat01-...``, ``ghp_...``, ``pypi-...``, ``Bearer ...``). Anything else a node prints
- an arbitrary string that is neither named nor shaped like a secret - reaches the
transcript unredacted. :func:`outcome_from_usage` is the pure translation from one
dispatch's terminal usage to the typed :class:`~agentdag.domain.models.NodeOutcome`
the coordinator branches on; it is unit-tested with no SDK call at all
(``tests/test_kernel_executor_claude.py``).

Contents:
    * :data:`DEFAULT_TOOLS` - the tool set a node gets when the caller does not override it.
    * :class:`CredentialSource` - what :class:`ClaudeExecutor` needs from a credential.
    * :class:`OAuthTokenFile` - a per-operator OAuth-token keyfile (the measured default).
    * :class:`CredentialCopy` - a private, owner-only copy of ``.credentials.json``.
    * :func:`append_transcript` - scrub and append one streamed message, owner-only.
    * :func:`input_total` - sum the three input-token fields a usage mapping carries.
    * :func:`outcome_from_usage` - pure: one dispatch's terminal usage -> :class:`NodeOutcome`.
    * :class:`ClaudeExecutor` - the :class:`~agentdag.application.kernel.ports.Executor` implementation.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage

from ...domain.errors import KernelError
from ...domain.models import ErrorType, NodeError, NodeOutcome, NodeStatus, Tokens
from ...domain.scrub import scrub
from .hooks_claude import deny_bash_commands, deny_outside_root

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from claude_agent_sdk import EffortLevel
    from claude_agent_sdk import HookCallback as _SdkHookCallback

    from ...application.kernel.ports import ExecutorRequest

__all__ = [
    "DEFAULT_TOOLS",
    "ClaudeExecutor",
    "CredentialCopy",
    "CredentialSource",
    "OAuthTokenFile",
    "append_transcript",
    "input_total",
    "outcome_from_usage",
]

DEFAULT_TOOLS = ("Read", "Edit", "Write", "Bash", "Grep", "Glob")
"""The tool set a node gets when :class:`ClaudeExecutor` is built with no override (M1's ``_TOOLS``)."""

_CONFIG_DIR_NAME = ".claude"
_CREDENTIALS_NAME = ".credentials.json"
_OWNER_ONLY_FILE = 0o600
_OWNER_ONLY_DIR = 0o700
_NO_VALUE = "-"
"""The sentinel :mod:`.dispatch` and this module both use for a field with no real value."""

# The TRUE allowlist child_env() builds the coordinator's PATH/LANG/proxy/CA-bundle/...
# from - never the whole environment, and never a guess at which OTHER names "look
# secret" (see _blank_everything_else, which blanks literally everything not listed
# here rather than pattern-matching names). Proxy and CA-bundle vars are here because a
# node genuinely needs them to reach the network through the same path the coordinator
# does; SYSTEMROOT/USERPROFILE/COMSPEC/PATHEXT/APPDATA/LOCALAPPDATA/WINDIR/SYSTEMDRIVE/
# PROGRAMDATA/HOMEDRIVE/HOMEPATH are Windows-only (harmless elsewhere, since
# _allowlisted_env() only copies a key that is actually set) - Node and the CLI itself
# read APPDATA/LOCALAPPDATA on Windows. UNVERIFIED LIVE: no D3/M2 probe has run this
# module on a Windows host, so this list is read from what a Windows Node/CLI process
# is documented to need, not measured against a real Windows dispatch.
_ALLOWLIST_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "TEMP",
    "TMP",
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
    "WINDIR",
    "SYSTEMDRIVE",
    "PROGRAMDATA",
    "HOMEDRIVE",
    "HOMEPATH",
)

_AUTH_FAILURE_TEXT = "Not logged in"

_EFFORT_LEVELS: tuple[EffortLevel, ...] = ("low", "medium", "high", "xhigh", "max")
"""Every value ``claude_agent_sdk.types.EffortLevel`` allows, read from source (0.2.139)."""


class CredentialSource(Protocol):
    """What :class:`ClaudeExecutor` needs from a credential: one node's own env slice."""

    def child_env(self, node_dir: Path) -> dict[str, str]:
        """Build one node's environment: the allowlist, ``HOME``, and this credential.

        Args:
            node_dir: The node's own directory; its isolated ``HOME``/config dir live
                under ``node_dir/home``.

        Returns:
            The env dict to fold into ``ClaudeAgentOptions.env`` - never
            ``os.environ`` itself, and never including a secret-shaped variable this
            adapter did not explicitly decide to forward.
        """
        ...


def _allowlisted_env() -> dict[str, str]:
    """Copy the coordinator's own :data:`_ALLOWLIST_KEYS`, skipping any not set."""
    return {key: os.environ[key] for key in _ALLOWLIST_KEYS if key in os.environ}


def _blank_everything_else(known: Mapping[str, str]) -> dict[str, str]:
    """Blank every environment variable this process inherited that ``known`` does not already carry.

    The TRUE allowlist's second half: a :class:`CredentialSource`'s own
    :meth:`~CredentialSource.child_env` decides exactly what a node's env carries
    (:data:`_ALLOWLIST_KEYS`, ``HOME``, the credential); this forces every OTHER
    inherited variable to ``""``, whatever its name looks like - not a pattern guess
    at which names "look secret" (an earlier version of this function matched names
    against a regex and missed ``SSH_AUTH_SOCK`` and ``AWS_ACCESS_KEY_ID`` outright).
    The SDK merges its own ``env`` OVER ``os.environ`` (M1 note) rather than replacing
    it, so the only way to actually drop an inherited variable from the child's
    environment is an explicit empty-string override here - omitting the key lets the
    merge read it straight from the coordinator's own environment. This is the SECOND
    of two layers; the FIRST is that the coordinator process itself must be started
    with a clean environment (Task 17), so a leak needs both to fail.

    Args:
        known: The env this dispatch actually decided to carry - every OTHER
            inherited variable is blanked.

    Returns:
        ``{name: "" for name in os.environ if name not in known}`` - merged UNDER
        ``known`` by :meth:`ClaudeExecutor._options_for`, so a name that legitimately
        appears in both (e.g. ``CLAUDE_CODE_OAUTH_TOKEN`` under :class:`OAuthTokenFile`,
        which is also this session's own env var when run interactively) keeps ITS
        value, never the blank default.
    """
    return {name: "" for name in os.environ if name not in known}


def _mkdir_owner_only(target: Path) -> Path:
    """Create ``target`` and any missing directory above it, each owner-only (``0700``).

    ``Path.mkdir(parents=True, mode=...)`` only applies ``mode`` to the leaf
    directory - any missing parent is created at the platform default (subject to
    umask), which can leave an intermediate level group- or other-readable. This
    recurses UPWARD, creating each missing level explicitly at ``0700``, and stops
    the first time it finds an ancestor that already exists. In production that is
    always ``node_dir`` itself: ``FsRunDir.node_dir()`` creates it, owner-only,
    before a node's body ever runs, so the recursion here only ever has to create
    ``home`` and ``home/.claude`` under an already-existing, already-owner-only
    directory. Mirrors ``run_store_fs.FsRunDir._mkdir_owner_only``'s own idea (there
    it walks DOWN from an already-owner-only root instead, since that root always
    exists by construction by the time it is called); kept as a local helper here
    rather than an import - :mod:`.run_store_fs` is a different adapter this module
    has no other reason to depend on.

    Args:
        target: The directory to ensure exists, owner-only, along with every
            missing ancestor above it.

    Returns:
        ``target``.
    """
    if not target.exists():
        _mkdir_owner_only(target.parent)
        with contextlib.suppress(FileExistsError):
            target.mkdir(mode=_OWNER_ONLY_DIR)
    return target


def _home_and_config_dir(node_dir: Path) -> tuple[Path, Path]:
    """Create and return ``(node_dir/home, node_dir/home/.claude)``, each owner-only (``0700``)."""
    home = node_dir / "home"
    config_dir = home / _CONFIG_DIR_NAME
    _mkdir_owner_only(config_dir)
    return home, config_dir


def _base_env(node_dir: Path) -> tuple[dict[str, str], Path]:
    """Build the allowlisted env every :class:`CredentialSource` starts from.

    Returns:
        The env (allowlist plus ``HOME``/``CLAUDE_CONFIG_DIR``) and the config dir path,
        so a caller can write its own credential material into it.
    """
    home, config_dir = _home_and_config_dir(node_dir)
    env = _allowlisted_env()
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env, config_dir


@dataclass(frozen=True, slots=True)
class OAuthTokenFile:
    """Credential source: a per-operator OAuth-token keyfile (D3's measured default).

    D3 (``workflow/design/probes/d3-subscription-terms.md``, RESEARCH) measured that
    ``CLAUDE_CODE_OAUTH_TOKEN`` authenticates an SDK child whose ``CLAUDE_CONFIG_DIR``
    is an EMPTY directory (arm A2, a stand-in for a real ``claude setup-token`` token;
    arm A itself stays UNMEASURED until the keyfile this class reads actually exists on
    the host it runs on). The file is read fresh on every :meth:`child_env` call rather
    than cached at construction, so a rotated token takes effect on the next node
    without restarting the coordinator.
    """

    path: Path

    def child_env(self, node_dir: Path) -> dict[str, str]:
        """Build this node's env: the allowlist, an EMPTY ``CLAUDE_CONFIG_DIR``, and the token.

        Raises:
            FileNotFoundError: :attr:`path` does not exist - the honest failure the
                CLI itself would otherwise report one dispatch later as "Not logged
                in", surfaced here immediately instead.
        """
        env, _config_dir = _base_env(node_dir)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = self.path.read_text(encoding="utf-8").strip()
        return env


@dataclass(frozen=True, slots=True)
class CredentialCopy:
    """Credential source: a private, owner-only COPY of a ``.credentials.json`` (D3 arm C).

    Mirrors ``work_claude_sdk.py``'s ``_copy_credential`` (M1): the copy is created with
    ``O_EXCL`` and mode ``0600`` in one step so the secret is never briefly
    world-readable, and an existing copy is left alone (the node may have refreshed its
    own token into it). Kept as the non-default :class:`CredentialSource` because the
    keyfile path (:class:`OAuthTokenFile`) removes the N-parallel-nodes-share-one-
    writable-login concern the original M1 note raised (D3's "Consequence for Task 14").
    """

    source_path: Path

    def child_env(self, node_dir: Path) -> dict[str, str]:
        """Build this node's env: the allowlist and a private copy of the credential file.

        Carries no ``CLAUDE_CODE_OAUTH_TOKEN`` - the CLI reads the copy from
        ``CLAUDE_CONFIG_DIR`` instead.
        """
        env, config_dir = _base_env(node_dir)
        _copy_credential(self.source_path, config_dir / _CREDENTIALS_NAME)
        return env


def _copy_credential(source: Path, destination: Path) -> None:
    """Copy ``source`` to ``destination`` once, owner-only, never overwriting.

    Args:
        source: The credential to copy; a missing one is not an error (the node then
            fails with the CLI's own "not logged in" message, the honest outcome).
        destination: Where the node's own copy goes.
    """
    if not source.is_file():
        return
    payload = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _OWNER_ONLY_FILE)
    except FileExistsError:
        return
    with os.fdopen(handle, "wb") as opened:
        opened.write(payload)


def _classify_error(text: str) -> NodeError:
    """Classify a failed dispatch's result text: the CLI's own login failure, or a generic executor error.

    ``text`` is scrubbed (:func:`~agentdag.domain.scrub.scrub`'s VALUE pass) before it
    becomes ``NodeError.message`` - the model's own final text is exactly the kind of
    string that could echo a secret shape back, and this message is what
    :meth:`Dispatcher._run_and_record` writes into ``record.json`` unscrubbed by
    anything else in that path.
    """
    scrubbed = cast("str", scrub(text))
    if _AUTH_FAILURE_TEXT in text:
        return NodeError(type=ErrorType.AUTH_FAILURE, message=scrubbed, transient=False)
    return NodeError(type=ErrorType.EXECUTOR_ERROR, message=scrubbed, transient=True)


def input_total(usage: Mapping[str, Any]) -> int:
    """Sum the three input-token fields a usage mapping carries: what the model just saw (design 3.8).

    Both ``ResultMessage.usage`` (the dispatch's terminal usage) and
    ``AssistantMessage.usage`` (one turn's usage, streamed) carry the same three
    fields; this is the one place that arithmetic is written, reused by
    :func:`outcome_from_usage` for the terminal figure and by
    :meth:`ClaudeExecutor._run` for ``first_turn_input_tokens``. Public (and tested
    directly, ``tests/test_kernel_executor_claude.py``) because it is a tested seam
    like :func:`outcome_from_usage`, not a private implementation detail.

    Args:
        usage: A usage mapping; each field defaults to 0 if this SDK version does
            not emit it.

    Returns:
        ``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``.

    Example:
        >>> input_total({"input_tokens": 50, "cache_creation_input_tokens": 3873, "cache_read_input_tokens": 119786})
        123709
    """
    return (
        int(usage.get("input_tokens", 0))
        + int(usage.get("cache_creation_input_tokens", 0))
        + int(usage.get("cache_read_input_tokens", 0))
    )


def outcome_from_usage(
    *,
    model: str,
    num_turns: int,
    is_error: bool,
    text: str,
    usage: Mapping[str, Any],
    first_turn_input: int,
    cwd_rel: str,
) -> NodeOutcome:
    """Translate one dispatch's terminal usage into the outcome the coordinator branches on.

    Pure: no SDK type crosses this boundary, so the whole translation is unit-tested
    without a model call. ``effort_used`` is set to :data:`_NO_VALUE`; a real effort
    value, when the request carries one AND it was validated before the dispatch, is
    folded in afterward by :meth:`ClaudeExecutor._outcome_for` (this function's
    signature has no ``effort`` parameter, per the brief it implements).

    Args:
        model: The model row the dispatch ran under; keys ``charged_tokens`` and
            stamps ``model_used``.
        num_turns: ``ResultMessage.num_turns``.
        is_error: ``ResultMessage.is_error``.
        text: ``ResultMessage.result`` (or ``""`` when the SDK reported none) - scanned
            for the CLI's own "Not logged in" text to name an auth failure specifically.
        usage: ``ResultMessage.usage`` (or ``{}``); only the four cache/token fields
            are read, each defaulting to 0 if this SDK version does not emit it.
        first_turn_input: :func:`input_total` of the FIRST ``AssistantMessage.usage``
            this dispatch saw (design 3.8: what the model just saw), recorded by
            :meth:`ClaudeExecutor._run` as it streams - a distinct figure from the
            terminal ``usage`` (the LAST turn's), kept in ``key_facts`` for M3's
            per-turn spend cap.
        cwd_rel: The node's working directory, relative to the isolation root - the
            sole ``artefact_refs`` entry on success, since a work node's artefact IS
            the tree it changed.

    Returns:
        ``status=DONE`` unless ``is_error``; on error, ``error.type=AUTH_FAILURE``
        (not transient) when ``text`` names the CLI's login failure, else
        ``EXECUTOR_ERROR`` (transient).

    Example:
        >>> outcome_from_usage(
        ...     model="sonnet", num_turns=1, is_error=False, text="ok",
        ...     usage={"input_tokens": 1, "cache_creation_input_tokens": 0,
        ...            "cache_read_input_tokens": 0, "output_tokens": 2},
        ...     first_turn_input=1, cwd_rel="wt/r",
        ... ).status
        <NodeStatus.DONE: 'done'>
    """
    in_tokens = input_total(usage)
    out_tokens = int(usage.get("output_tokens", 0))
    cache_read = int(usage.get("cache_read_input_tokens", 0))
    tokens = Tokens(**{"in": in_tokens, "out": out_tokens, "cache_read": cache_read, "reasoning": None})
    status = NodeStatus.FAILED if is_error else NodeStatus.DONE
    return NodeOutcome(
        status=status,
        artefact_refs=[] if is_error else [cwd_rel],
        key_facts={"turns": num_turns, "first_turn_input_tokens": first_turn_input},
        typed_fields=["turns"],
        tokens=tokens,
        charged_tokens={model: in_tokens + out_tokens},
        executor_used="claude",
        model_used=model,
        effort_used=_NO_VALUE,
        error=_classify_error(text) if is_error else None,
    )


def _as_sdk_hooks(callback: object) -> list[_SdkHookCallback]:
    """Bridge one of :mod:`.hooks_claude`'s hooks into ``HookMatcher.hooks``' declared type.

    :mod:`.hooks_claude` deliberately types a hook's input as a plain ``dict[str, Any]``
    and its return as ``dict[str, Any]`` (so it is unit-testable with a synthetic dict
    and no SDK types at all - see that module's own docstring), while the SDK's own
    ``HookCallback`` types the input as ``HookInput`` (a union of TypedDicts) and the
    return as ``Awaitable[HookJSONOutput]`` (also a union of TypedDicts). Verified
    directly against pyright (not assumed) that the two callable shapes are not
    STRUCTURALLY compatible, on TWO independent grounds: parameter CONTRAVARIANCE fails
    because pyright treats a TypedDict as NOT assignable to ``dict[str, Any]``
    (invariant, mutable - a wider ``dict[str, Any]`` view would let a caller insert an
    arbitrary key, violating the TypedDict's closed key set); return-type COVARIANCE
    fails for the mirror-image reason, since ``Awaitable``'s type parameter IS
    covariant and ``dict[str, Any]`` is the WIDER type here, not a subtype of the
    narrower ``HookJSONOutput``. Both hold even though every concrete value the SDK
    ever passes or expects at runtime - a TypedDict IS a dict - satisfies the wider
    ``dict[str, Any]`` shape just fine (the M2 probe, ``workflow/design/probes/
    m2-hooks-dontask.md`` in RESEARCH, measured exactly this: the SDK invokes both
    hooks and respects their decision). This cast documents that the mismatch is a
    Python type-system limitation at this boundary, not a real behavioural gap.

    Args:
        callback: A hook built by :func:`~.hooks_claude.deny_outside_root` or
            :func:`~.hooks_claude.deny_bash_commands`.

    Returns:
        A one-element list, typed as ``HookMatcher.hooks`` declares it.
    """
    return [cast("_SdkHookCallback", callback)]


def _message_to_jsonable(message: object) -> dict[str, Any]:
    """Render one streamed SDK message as a JSON-safe dict, tagged with its class name."""
    raw: dict[str, Any] = (
        asdict(message) if is_dataclass(message) and not isinstance(message, type) else {"repr": repr(message)}
    )
    return {"type": type(message).__name__, **raw}


def _validated_effort(effort: str | None) -> EffortLevel | None:
    """Validate ``request.effort`` against the SDK's own allowed values, BEFORE the dispatch.

    Args:
        effort: ``ExecutorRequest.effort`` - ``None`` when the caller did not ask for
            a specific effort at all (not an error: the model then runs at whatever
            its own default is).

    Returns:
        ``effort`` unchanged when it is one of :data:`_EFFORT_LEVELS`, or ``None``
        when the caller did not ask for one. No cast needed here: pyright narrows a
        plain ``str`` to the ``Literal`` union ``EffortLevel`` from the
        ``if effort not in _EFFORT_LEVELS: raise`` guard alone (verified directly -
        adding an explicit ``cast`` after it is flagged ``reportUnnecessaryCast``),
        so this function is the ONE place that proves, at runtime AND to the type
        checker, that the plain ``str`` :attr:`ExecutorRequest.effort` field actually
        IS one of the SDK's known values before treating it as one.

    Raises:
        KernelError: ``effort`` is set but is not one of the SDK's known effort
            levels - a config bug in whatever built the request, caught before any
            model call spends anything, not a transient failure the SDK could cause.
    """
    if effort is None:
        return None
    if effort not in _EFFORT_LEVELS:
        raise KernelError(f"unknown effort level {effort!r}; must be one of {_EFFORT_LEVELS}")
    return effort


def _cwd_rel(request: ExecutorRequest) -> str:
    """Compute ``request.cwd`` relative to ``request.isolation_root``, POSIX-style, BEFORE the dispatch.

    Raises:
        KernelError: ``request.cwd`` is not under ``request.isolation_root`` - a
            config bug in whatever built the request (design 2.1, C8: a work node's
            ``cwd`` is always supposed to be a path under its own isolation root),
            caught before the model call spends anything rather than surfacing as a
            bare ``ValueError`` after a completed dispatch has already paid for it.
    """
    try:
        return request.cwd.relative_to(request.isolation_root).as_posix()
    except ValueError as exc:
        raise KernelError(
            f"request.cwd {request.cwd} is not under request.isolation_root {request.isolation_root}"
        ) from exc


@dataclass(frozen=True, slots=True)
class ClaudeExecutor:
    """Runs one kernel node as a Claude Agent SDK client, per design 7 and the M2 probe.

    Attributes:
        credentials: Where this node's login comes from - :class:`OAuthTokenFile` (the
            default) or :class:`CredentialCopy`.
        deny_bash: The Bash command denylist, matched as substrings after whitespace
            collapsing (:func:`~agentdag.adapters.kernel.hooks_claude.deny_bash_commands`);
            the run-wide default lives in config (``[kernel] deny_bash``), and a request
            may carry a different one (``ExecutorRequest.deny_bash``) - THIS field is
            the executor's own fallback when the caller does not thread the request's
            through per-call (kept keyword-only so it is never confused with the
            positional ``credentials``).
        tools: The tool set a node may call, matched against
            ``ClaudeAgentOptions.allowed_tools``. Defaults to :data:`DEFAULT_TOOLS`.
    """

    credentials: CredentialSource
    deny_bash: tuple[str, ...] = field(kw_only=True)
    tools: tuple[str, ...] = field(default=DEFAULT_TOOLS, kw_only=True)

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Validate ``request``, then run it to completion and report its outcome.

        ``request.cwd``/``request.effort`` are checked BEFORE the broad exception
        guard below, not inside it: both a cwd outside the isolation root and an
        unknown effort level are config bugs in whatever BUILT the request, never a
        transient executor failure a retry could fix, so :func:`_cwd_rel` and
        :func:`_validated_effort` raise :class:`~agentdag.domain.errors.KernelError`
        straight out of this method - :mod:`.dispatch`'s own outer ``Exception`` guard
        (``_run_body``) still turns that into a record one layer up (a raising body is
        always a record there, never a dead run), but THIS method never mislabels a
        config bug as ``executor_error``/``transient=True``, which would tell a
        retrier to try the identical bad request again.

        Any OTHER exception - the model call is the one genuinely external edge here
        (M1's docstring) - becomes a FAILED outcome with ``executor_error``
        (transient), never past this method: this is the first of the two guards
        described above, and the only place that can report the MORE SPECIFIC
        ``auth_failure`` when the SDK itself completes but reports one.

        Raises:
            KernelError: ``request.cwd`` is not under ``request.isolation_root``, or
                ``request.effort`` is set but names no known SDK effort level - both
                checked before ``ClaudeSDKClient`` is ever constructed.
        """
        cwd_rel = _cwd_rel(request)
        _validated_effort(request.effort)  # side effect only here: raise on an unknown value, nothing to keep
        try:
            return await self._run(request, cwd_rel)
        except Exception as exc:  # the external edge: never let it past this node
            return NodeOutcome(
                status=NodeStatus.FAILED,
                executor_used="claude",
                model_used=request.model,
                # Never request.effort here: reaching this branch means the SDK
                # dispatch itself did not complete (request.cwd/request.effort are
                # already known good by this point - see the Raises note above), so
                # there is no dispatch this record could truthfully say ran under a
                # given effort. Only _outcome_for, on a real ResultMessage, ever
                # stamps request.effort.
                effort_used=_NO_VALUE,
                # scrub()'s VALUE pass: an exception string CAN carry a header (an SDK
                # HTTP client's own error text sometimes echoes an Authorization value)
                # that never went anywhere near this module's own KEY-based redaction.
                error=NodeError(
                    type=ErrorType.EXECUTOR_ERROR,
                    message=cast("str", scrub(f"{type(exc).__name__}: {exc}")),
                    transient=True,
                ),
            )

    async def _run(self, request: ExecutorRequest, cwd_rel: str) -> NodeOutcome:
        """Do the real work of :meth:`run`, inside its exception guard.

        ``cwd_rel`` is a parameter, computed by :meth:`run` BEFORE this method is even
        called (and before its own try/except), so a bad one never reaches here.
        """
        options = self._options_for(request)
        transcript_path = request.node_dir / "transcript.jsonl"
        first_turn_input = 0
        seen_first_turn = False
        async with ClaudeSDKClient(options=options) as client:
            await client.query(request.prompt)
            async for message in client.receive_response():
                append_transcript(transcript_path, message)
                if isinstance(message, AssistantMessage):
                    usage = message.usage or {}
                    self._on_turn(usage)
                    if not seen_first_turn:
                        first_turn_input = input_total(usage)
                        seen_first_turn = True
                if isinstance(message, ResultMessage):
                    return self._outcome_for(request, message, first_turn_input, cwd_rel)
        return NodeOutcome(
            status=NodeStatus.FAILED,
            executor_used="claude",
            model_used=request.model,
            effort_used=_NO_VALUE,  # the dispatch never produced a ResultMessage; never claim it ran
            error=NodeError(type=ErrorType.EXECUTOR_ERROR, message="no ResultMessage", transient=True),
        )

    def build_options_env(self, request: ExecutorRequest) -> dict[str, str]:
        """Validate ``request.effort`` and build this dispatch's TRUE-allowlisted env.

        The pure half of :meth:`_options_for`, hoisted out into its own public method
        so a test can drive it directly (no ``# pyright: ignore[reportPrivateUsage]``
        needed - it is a tested seam like :func:`outcome_from_usage`), and so a future
        caller that only needs the env (not a full ``ClaudeAgentOptions``) has one.

        Raises:
            KernelError: ``request.effort`` is set but is not one of the SDK's known
                effort levels (:func:`_validated_effort`) - validated here too so a
                caller driving only this method still gets the same fail-before-
                dispatch guarantee :meth:`_options_for` gives; the validated value
                itself is discarded, since this method's own return is the env, not
                the effort (:meth:`_options_for` re-validates to get that value, an
                idempotent, side-effect-free re-check, not a second real validation).
        """
        _validated_effort(request.effort)
        known = self.credentials.child_env(request.node_dir)
        return {**_blank_everything_else(known), **known}

    def _options_for(self, request: ExecutorRequest) -> ClaudeAgentOptions:
        """Build this dispatch's SDK options: hooks, credential, a TRUE-allowlisted env, a validated effort."""
        effort = _validated_effort(request.effort)  # raises KernelError BEFORE the call on an unknown value
        env = self.build_options_env(request)
        return ClaudeAgentOptions(
            cwd=str(request.cwd),
            system_prompt=request.brief,
            setting_sources=[],
            model=request.model,
            effort=effort,
            max_turns=request.max_turns,
            permission_mode="dontAsk",
            allowed_tools=list(self.tools),
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        matcher="Write|Edit|MultiEdit|NotebookEdit",
                        hooks=_as_sdk_hooks(deny_outside_root(request.isolation_root)),
                    ),
                    HookMatcher(
                        matcher="Bash", hooks=_as_sdk_hooks(deny_bash_commands(request.deny_bash or self.deny_bash))
                    ),
                ]
            },
            env=env,
        )

    def _outcome_for(
        self, request: ExecutorRequest, message: ResultMessage, first_turn_input: int, cwd_rel: str
    ) -> NodeOutcome:
        """Translate a terminal :class:`ResultMessage` via :func:`outcome_from_usage`, effort folded in.

        ``cwd_rel`` is a parameter, not recomputed here, because it must be validated
        (:func:`_cwd_rel`) BEFORE the model call in :meth:`_run`, not after - by the
        time this method runs, the value is already known good.
        """
        outcome = outcome_from_usage(
            model=request.model,
            num_turns=message.num_turns,
            is_error=message.is_error,
            text=message.result or "",
            usage=message.usage or {},
            first_turn_input=first_turn_input,
            cwd_rel=cwd_rel,
        )
        if request.effort:
            # Safe to stamp the REQUESTED value here specifically: _options_for already
            # validated it (via _validated_effort) with no raise, earlier in this same
            # dispatch, so the SDK call this ResultMessage came from actually carried it.
            outcome = outcome.model_copy(update={"effort_used": request.effort})
        return outcome

    def _on_turn(self, usage: dict[str, Any]) -> None:
        """Per-``AssistantMessage`` hook point; a no-op here.

        M3 fills this in with the per-turn spend check and ``client.interrupt()`` call
        (design 7's "the token cap has two call sites"); this task only creates the
        seam and records ``first_turn_input_tokens`` (done by :meth:`_run` itself, not
        by this method). This method's OWN signature will need to change for that: a
        spend check that decides to stop the dispatch needs the live ``client`` handle
        (to call ``client.interrupt()``) or some other stop signal ``_run`` can act on
        after this call returns - ``usage`` alone cannot express "stop now" back to the
        caller. Not built here; :meth:`_run` calls this once per turn with only
        ``usage`` because that is all M2 needs.
        """


def append_transcript(path: Path, message: object) -> None:
    """Scrub and append one streamed SDK message to ``path`` as a single JSON line, owner-only (``0600``).

    Public (and tested directly, ``tests/test_kernel_executor_claude.py`` and
    ``tests/test_kernel_secrets.py``) because it is a tested seam like :func:`scrub`
    and :func:`outcome_from_usage`, not a private implementation detail.

    Non-``str`` leaves ``json.dumps`` would otherwise need ``default=str`` for (a
    ``Path``, a ``datetime``, an SDK type with no ``dict`` shape) are converted to
    their string form FIRST, via ``json.loads(json.dumps(obj, default=str))``, and
    :func:`~agentdag.domain.scrub.scrub` runs on the RESULT of that round-trip, not on
    the raw object graph. Order matters: ``scrub``'s VALUE pass only ever inspects
    ``str`` values, so a secret-shaped leaf that reaches ``scrub`` as some other type
    (and would only become a matchable string once ``default=str`` stringifies it)
    would escape the VALUE pass entirely if the stringification happened AFTER
    scrubbing instead of before.

    Opened with ``os.open`` rather than :meth:`~pathlib.Path.open` because
    ``Path.open("a")`` creates a NEW file at whatever mode ``0666`` minus the
    process umask works out to - typically ``0644``, group- and other-readable.
    ``O_CREAT`` with an explicit mode fixes the file's permissions at CREATION time,
    the same reasoning ``run_store_fs.FsRunDir._write_temp_file`` uses (there via
    ``fchmod`` right after creating the temp file, since it needs
    ``tempfile.NamedTemporaryFile``'s own naming; here a plain ``os.open`` can just
    ask for the right mode directly). A pre-existing file (a crash-window rerun
    reopening the same transcript) keeps its already-owner-only mode; ``O_CREAT``
    without ``O_EXCL`` does not re-chmod it, which is fine since it was created this
    same way originally. ``os.fdopen`` (rather than a bare ``os.write``/``os.close``
    pair) is what :func:`_copy_credential` already uses to write a whole payload in
    one call: a short write on a partial-write filesystem condition must not silently
    truncate a JSONL line, and wrapping the fd in a buffered file object is what makes
    ``.write`` retry internally until the whole payload lands (or raises).
    """
    normalized = json.loads(json.dumps(_message_to_jsonable(message), default=str))
    line = scrub(normalized)
    payload = (json.dumps(line) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _OWNER_ONLY_FILE)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
