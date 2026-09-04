"""The Claude kernel executor: allowlisted env, a per-node credential, PreToolUse
hooks, and tokens that mean what they say (design 7, M2 probe).

Each node gets its own :class:`~claude_agent_sdk.ClaudeSDKClient`, run under
``permission_mode="dontAsk"`` with the two hooks :mod:`.hooks_claude` builds -
``deny_outside_write_set`` matched against ``Write|Edit|MultiEdit|NotebookEdit``,
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
of two sources (D3, RESEARCH): :class:`CredentialCopy` (D3 arm C: a private,
owner-only copy of the operator's ``.credentials.json``, mirroring M1's
``_copy_credential``), which is the SHIPPED default because
``[credentials] claude_oauth_token_file`` ships empty; or :class:`OAuthTokenFile`,
used when that key names an existing keyfile (D3 arm A2 proved
``CLAUDE_CODE_OAUTH_TOKEN`` authenticates a child whose ``CLAUDE_CONFIG_DIR`` is
empty).

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
    * :class:`OAuthTokenFile` - a per-operator OAuth-token keyfile, when config names one.
    * :class:`CredentialCopy` - a private, owner-only copy of ``.credentials.json``; the
      shipped default.
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

from ...domain.handover import HANDOVER_FILENAME
from ...domain.kernel_errors import KernelError
from ...domain.models import CredentialVerdict, ErrorType, NodeError, NodeOutcome, NodeStatus, Tokens
from ...domain.scrub import scrub
from .clock_utc import UtcClock
from .credential_probe import NoCredentialProbe
from .hooks_claude import (
    deny_bash_commands,
    deny_closed_tools,
    deny_every_bash_command,
    deny_outside_write_set,
    deny_reads_outside,
    inject_stop_notice,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime
    from pathlib import Path

    from claude_agent_sdk import EffortLevel
    from claude_agent_sdk import HookCallback as _SdkHookCallback

    from ...application.kernel.ports import Clock, CredentialProbe, ExecutorRequest

__all__ = [
    "DEFAULT_TOOLS",
    "ClaudeExecutor",
    "CredentialCopy",
    "CredentialSource",
    "OAuthTokenFile",
    "append_transcript",
    "charged_total",
    "input_total",
    "outcome_from_usage",
    "separated_refusal",
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


class _Interruptible(Protocol):
    """What :meth:`ClaudeExecutor._on_turn` needs from the live client: only ``interrupt()``.

    A narrower seam than the concrete :class:`~claude_agent_sdk.ClaudeSDKClient` this
    method is actually handed (:meth:`ClaudeExecutor._run` calls it with the real
    client) - a real client structurally satisfies this Protocol, and a unit test can
    hand it a bare double with one method and no cast, the same reasoning
    :class:`CredentialSource` below already applies to the credential seam.
    """

    async def interrupt(self) -> None:
        """Stop the in-flight dispatch; the SDK's own ``ClaudeSDKClient.interrupt``."""
        ...


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

    def bearer_token(self) -> str | None:
        """Return this credential's OAuth access token, for a direct call to the API.

        Only the credential PROBE needs this: the executor itself never handles the token,
        it hands the CLI an env slice and lets the CLI read it. ``None`` whenever the token
        cannot be produced (the file is missing, or its shape is not one this source knows),
        which the probe reads as "no evidence" rather than as any particular verdict.
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
        The env (allowlist plus ``HOME``/``CLAUDE_CONFIG_DIR``/``REMEMBER_PROMPT_STAMP``)
        and the config dir path, so a caller can write its own credential material into it.
    """
    home, config_dir = _home_and_config_dir(node_dir)
    env = _allowlisted_env()
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    # An EXPLICIT positive value, not a name to allowlist through from the coordinator's
    # own environment (which does not set this) and not a config-file setting (the
    # operator's `remember` plugin hook reads this ENV VAR directly, before its config
    # resolution runs). Must be non-empty too: the hook's own
    # `${REMEMBER_PROMPT_STAMP:-full}` substitutes its default on unset OR EMPTY, and
    # _blank_everything_else() below would otherwise blank this name to "" like every
    # other inherited variable this dispatch does not carry - indistinguishable, to the
    # hook, from never having been set. The default `full` stamp carries a wall clock and
    # a live context percentage, both of which change on every dispatch and defeat
    # prompt-cache reuse for everything the stamp precedes; `stable` is byte-stable but
    # keeps the one signal that matters, a threshold-gated context warning.
    env["REMEMBER_PROMPT_STAMP"] = "stable"
    return env, config_dir


@dataclass(frozen=True, slots=True)
class OAuthTokenFile:
    """Credential source: a per-operator OAuth-token keyfile, used when config names one that exists.

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

    def bearer_token(self) -> str | None:
        """Return the keyfile's contents, which ARE the token; ``None`` if it cannot be read."""
        try:
            return self.path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None


@dataclass(frozen=True, slots=True)
class CredentialCopy:
    """Credential source: a private, owner-only COPY of a ``.credentials.json`` (D3 arm C).

    Mirrors ``work_claude_sdk.py``'s ``_copy_credential`` (M1): the copy is created with
    ``O_EXCL`` and mode ``0600`` in one step so the secret is never briefly
    world-readable, and an existing copy is left alone (the node may have refreshed its
    own token into it). This is the SHIPPED default :class:`CredentialSource`:
    ``[credentials] claude_oauth_token_file`` ships empty, so the run command's credential
    resolver lands here unless an operator names an existing keyfile. D3 preferred the
    keyfile (:class:`OAuthTokenFile`), which removes the N-parallel-nodes-share-one-
    writable-login concern the original M1 note raised (D3's "Consequence for Task 14");
    that preference is not what ships. Consequence: every node home under a run directory
    holds a copy of the operator's live credential, so a run directory is never
    publishable raw.
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

    def bearer_token(self) -> str | None:
        """Return the access token inside the ``.credentials.json`` this copies.

        Reads the ORIGINAL rather than any node's copy: a node directory is per-dispatch and
        the probe runs for the executor as a whole. Every failure - unreadable, not JSON, a
        shape without the token - is ``None``, because the probe's job is to add evidence and
        a guess about an unrecognised shape is not evidence.
        """
        try:
            parsed: object = json.loads(self.source_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        oauth: object = cast("dict[str, object]", parsed).get("claudeAiOauth")
        if not isinstance(oauth, dict):
            return None
        token: object = cast("dict[str, object]", oauth).get("accessToken")
        return token if isinstance(token, str) and token else None


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


_MAX_TURNS_SUBTYPE = "error_max_turns"
"""``ResultMessage.subtype`` when the dispatch used every turn ``max_turns`` allowed.

Read from a real failed dispatch's transcript, 2026-09-02, not from documentation: six work
nodes on the first `spec`-scale run each ended with this subtype at ``num_turns`` one past the
configured ceiling. The SDK names the same constant in its own error mapping."""


def charged_total(usage: Mapping[str, Any]) -> int:
    """Sum what a budget CHARGES: ``input_tokens + cache_creation_input_tokens + output_tokens``.

    It excludes exactly one field, ``cache_read_input_tokens``, and that exclusion is the point.
    A cached prefix is re-read on every turn, so charging it makes a limit grow with how LONG a
    conversation got rather than with how much work was done: measured 2026-09-02 on one real work
    node, 1,132,340 charged the old way against 66,665 of new context, a factor of 17.0. A ceiling
    in the old unit therefore bound conversation length, not spend.

    Output IS charged, and that is a deliberate divergence from `agentswarm`'s evaluation protocol,
    which excludes it. Its reason does not apply here: it reads the streamed ``message_start``
    usage, which under-reports output by orders of magnitude, while this reads the terminal
    ``ResultMessage`` usage, which is the cumulative dispatch total and accurate. Output is also
    the expensive half - the shipped policy prices it at 5x input on every row - so a budget
    without it under-counts precisely where cost concentrates.

    ``input_total`` remains the right figure for the CONTEXT question - what the model just saw,
    which decides the handover ceiling (design 3.8) - and is unchanged. The two answer different
    questions and one piece of arithmetic cannot serve both.

    Args:
        usage: ``ResultMessage.usage`` or one turn's ``AssistantMessage.usage``.

    Returns:
        ``input_tokens + cache_creation_input_tokens + output_tokens``.

    Example:
        >>> charged_total({"input_tokens": 50, "cache_creation_input_tokens": 3873,
        ...                "cache_read_input_tokens": 119786, "output_tokens": 1034})
        4957
    """
    return (
        int(usage.get("input_tokens", 0))
        + int(usage.get("cache_creation_input_tokens", 0))
        + int(usage.get("output_tokens", 0))
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
    subtype: str = "",
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
        subtype: ``ResultMessage.subtype``. Only one value is read, and it is read
            BEFORE ``is_error``: ``error_max_turns`` means the dispatch used every turn
            it was allowed, which is a CEILING being reached rather than a fault.

    Returns:
        ``status=NEEDS_CONTINUATION`` when the turn ceiling was reached;
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
    # The turn ceiling is read BEFORE is_error, because the CLI reports it AS an error
    # (is_error true, subtype "error_max_turns") and it is not one. It is the same class of
    # event as crossing `handover_at_tokens`: a bound the operator set, reached. Treating it
    # as EXECUTOR_ERROR - which is TRANSIENT, so the retry path re-dispatches into the same
    # wall - is what made six work nodes die identically and namelessly on the first real
    # run of this path, 2026-09-02, each after spending its whole budget.
    turns_exhausted = subtype == _MAX_TURNS_SUBTYPE
    status = NodeStatus.NEEDS_CONTINUATION if turns_exhausted else (NodeStatus.FAILED if is_error else NodeStatus.DONE)
    keeps_tree = turns_exhausted or not is_error
    return NodeOutcome(
        status=status,
        artefact_refs=[cwd_rel] if keeps_tree else [],
        key_facts={
            "turns": num_turns,
            "first_turn_input_tokens": first_turn_input,
            "turns_exhausted": turns_exhausted,
        },
        # `turns_exhausted` is TYPED for the same reason `grace_expired` is on a handover:
        # it is this outcome's decisive fact and a condition may branch only on a typed key.
        typed_fields=["turns", "turns_exhausted"],
        tokens=tokens,
        charged_tokens={model: charged_total(usage)},
        executor_used="claude",
        model_used=model,
        effort_used=_NO_VALUE,
        error=None if turns_exhausted else (_classify_error(text) if is_error else None),
    )


HANDOVER_GRACE_TURNS = 3
"""How many further API REQUESTS a node gets to write its handover after being asked to (design 3.8).

Bounded rather than open-ended because compliance is not guaranteed: the probe behind
decision 14 measured 4 of 4 under the right framing and 0 of 4 under the wrong one, so a
node that simply carries on must still be stopped. Three is enough for a node to finish its
current tool call, write one JSON file and report - and short enough that an ignoring node
does not spend a whole extra window past its ceiling.

Three is also measured rather than chosen: every complying node in the grace probe's 58
dispatches wrote its handover within TWO requests of the notice, and the one-request grace
lost the record 8 times out of 8. See :meth:`_Handover.observe` for why the unit matters.
"""


@dataclass
class _Handover:
    """The context-ceiling handover's own state across one dispatch's turns (design 3.8).

    A small object rather than three locals in :meth:`ClaudeExecutor._run` because the
    arm-then-grace rule is a state machine, and inlining it pushed that method past its
    branch limit. It also gives the stop-notice hook something stable to read: the hook is
    installed before the dispatch starts and armed part-way through it.
    """

    armed: bool = False
    """Whether the node has been asked to hand over. The hook's predicate reads this."""

    grace_used: int = 0
    """API requests seen since arming; the node is interrupted once this reaches the grace."""

    context_at: int = 0
    """The context of the turn that armed the handover, recorded for the record's key_facts.

    For a ceiling handover that IS the turn that crossed it. For a subtree stop it is simply
    where the node's context stood when it was told to stop - still worth recording as a
    drift signal, but it crossed nothing."""

    by_subtree: bool = False
    """Whether the SUBTREE stopping armed this handover, rather than the context ceiling."""

    armed_request: str | None = None
    """The request that crossed the ceiling. Its own later blocks must not spend the grace."""

    last_counted: str | None = None
    """The last request counted, so a request's remaining blocks are not counted again."""

    @property
    def expired(self) -> bool:
        """Whether the grace ran out, which is the same thing as ``interrupt()`` having been called.

        Derived rather than latched: :meth:`observe` returns True on exactly the request that
        reaches the grace, and :meth:`ClaudeExecutor._run` calls ``interrupt()`` on that return
        and nowhere else for this reason, so ``grace_used`` already IS the answer. A separate
        flag set beside the call would be a second source for one fact.

        Examples:
            >>> _Handover(armed=True, grace_used=HANDOVER_GRACE_TURNS).expired
            True
            >>> _Handover(armed=True, grace_used=HANDOVER_GRACE_TURNS - 1).expired
            False
        """
        return self.grace_used >= HANDOVER_GRACE_TURNS

    def observe(
        self,
        usage: Mapping[str, Any],
        ceiling: int | None,
        *,
        request_id: str | None,
        stop_requested: bool = False,
    ) -> bool:
        """Fold one turn in, and say whether the dispatch should now be interrupted.

        Arming does NOT interrupt: the node is being asked to WRITE its handover, so
        stopping it at the moment of asking guarantees no record exists for the successor
        (decision 14). Only the grace running out interrupts.

        Counted per API REQUEST, never per streamed event. This CLI emits one
        ``AssistantMessage`` per CONTENT BLOCK, each repeating that request's own
        ``message_id`` and usage, so a per-event fold is spent by the single turn that armed
        it - the same double count ``dbb5c9e`` fixed for the token sums, which is why the
        key is the same one. Measured (RESEARCH
        ``workflow/design/probes/handover-grace-expiry.md``, 58 dispatches): a complying
        node needs two requests after the notice and takes three events doing it, so a
        three-EVENT grace lands exactly on that boundary and lost the record 1 time in 8.

        An event carrying no id cannot be attributed to a request, so it counts once on its
        own, exactly as the spend counter treats one.

        Args:
            usage: This turn's own usage.
            ceiling: The row's ``handover_at_tokens``, or None when it declares none.
            request_id: This event's ``message_id``, or None when it carries none.
            stop_requested: Whether this node's SUBTREE has asked it to stop
                (``ExecutorRequest.is_stopping``). ORed with the ceiling into ONE arming
                decision, so both reasons spend the same measured grace and produce the
                same record shape; once armed, neither reason can arm it again, so this is
                read only on the arming turn. Defaults False, which is what a dispatch
                belonging to no subtree passes.

        Returns:
            Whether to interrupt now.
        """
        if not self.armed:
            if not (stop_requested or _past_context_ceiling(usage, ceiling)):
                return False
            self.armed = True
            # Subtree first when a turn triggers both: a node whose subtree is stopping is
            # having its plan REPLACED, while one that merely crossed its ceiling is being
            # continued, so recording the ceiling here would let a re-plan read an
            # abandoned node as an ordinary continuation.
            self.by_subtree = stop_requested
            self.context_at = input_total(usage)
            self.armed_request = request_id
            return False
        key = request_id or f"unkeyed-{self.grace_used}"
        if key in (self.armed_request, self.last_counted):
            return False
        self.last_counted = key
        self.grace_used += 1
        return self.grace_used >= HANDOVER_GRACE_TURNS


def _subtree_stopping(request: ExecutorRequest) -> bool:
    """Whether this node's subtree has asked it to stop, for a request that names one.

    A function rather than an inline ``request.is_stopping is not None and ...`` so the
    turn seam's arming condition stays one line per reason, and so "no subtree" and "a
    subtree that is not stopping" are answered in ONE place as the same False.

    Args:
        request: The dispatch's request; ``is_stopping`` is None for a call site outside
            any plan, which is not the same thing as a subtree that has not stopped, but
            arms nothing either way.

    Returns:
        Whether the node should be asked to hand over on subtree grounds.

    Examples:
        >>> from pathlib import Path
        >>> from agentdag.application.kernel.ports import ExecutorRequest
        >>> def _req(pred):
        ...     return ExecutorRequest(
        ...         node_dir=Path("n"), cwd=Path("c"), brief="b", prompt="p", model="sonnet",
        ...         effort=None, max_turns=1, isolation_root=Path("r"), write_set=(),
        ...         deny_bash=(), is_stopping=pred,
        ...     )
        >>> _subtree_stopping(_req(None))
        False
        >>> _subtree_stopping(_req(lambda: False))
        False
        >>> _subtree_stopping(_req(lambda: True))
        True
    """
    return request.is_stopping is not None and request.is_stopping()


def _past_context_ceiling(usage: Mapping[str, Any], ceiling: int | None) -> bool:
    """Whether THIS turn's own context has passed the row's ``handover_at_tokens`` (design 3.8).

    Reads one turn's ``input_total`` - what the model just saw - and never a sum across
    turns. The two questions are different: a SPEND cap asks how much this dispatch has
    used in total, which only a sum can answer; a CONTEXT ceiling asks how full the window
    is right now, which only a single turn's own figure can answer. Feeding a running sum
    to this comparison would stop a long dispatch whose window is nearly empty.

    Args:
        usage: One ``AssistantMessage.usage``.
        ceiling: The row's ``handover_at_tokens``, or ``None`` when it declares none - in
            which case nothing is checked, matching the same rule the token cap and the
            deadline follow for an absent bound.

    Returns:
        Whether the turn's context is STRICTLY past the ceiling. Inclusive like the token
        cap: a turn landing exactly on the ceiling does not trigger a handover, so a
        ceiling a node's every turn reaches exactly still lets it finish.

    Example:
        >>> _past_context_ceiling({"input_tokens": 120}, 100)
        True
        >>> _past_context_ceiling({"input_tokens": 100}, 100)
        False
        >>> _past_context_ceiling({"input_tokens": 10_000}, None)
        False
    """
    if ceiling is None:
        return False
    return input_total(usage) > ceiling


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
        callback: A hook built by :func:`~.hooks_claude.deny_outside_write_set` or
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


def allowed_writes(request: ExecutorRequest) -> tuple[str, ...]:
    """Return the globs this node may write to, relative to its isolation root.

    Its declared ``write_set``, plus its OWN node directory. The second is a grant rather
    than something every workflow must remember to declare: ``nodes/<node_id>/<hash8>/``
    is where the dispatcher already writes this node's brief, input and record, and where
    an artefact a node produces for itself belongs. Nothing else is added - a sibling's
    worktree and the run's own bookkeeping are somebody else's to write.

    Args:
        request: The dispatch request, already carrying the node's write set.

    Returns:
        The globs, relative to ``request.isolation_root``, POSIX-style.

    Raises:
        KernelError: ``request.node_dir`` is not under ``request.isolation_root``, which
            would mean the request was built wrong - the same class of bug
            :func:`_cwd_rel` refuses, and refused here rather than silently granting a
            path outside the root.

    Example:
        >>> from pathlib import Path
        >>> from agentdag.application.kernel.ports import ExecutorRequest
        >>> request = ExecutorRequest(
        ...     node_dir=Path("/r/nodes/w1/0000abcd"), cwd=Path("/r/wt/a"), brief="b", prompt="p",
        ...     model="sonnet", effort=None, max_turns=1, isolation_root=Path("/r"),
        ...     write_set=("wt/a/**",), deny_bash=(),
        ... )
        >>> allowed_writes(request)
        ('wt/a/**', 'nodes/w1/0000abcd/**')
    """
    try:
        node_rel = request.node_dir.relative_to(request.isolation_root).as_posix()
    except ValueError as exc:
        raise KernelError(
            f"request.node_dir {request.node_dir} is not under request.isolation_root {request.isolation_root}"
        ) from exc
    return (*request.write_set, f"{node_rel}/**")


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


async def separated_refusal(outcome: NodeOutcome, probe: CredentialProbe) -> NodeOutcome:
    """Re-label an auth-shaped failure as a rate limit when the provider says it is one.

    The CLI cannot tell the operator which happened - measured 2026-08-24, three dispatches
    reported ``authentication_failed`` and "Not logged in" while the same credential returned
    HTTP 429 from the API in the same minute, with ``api_error_status`` null and ``errors``
    null. So the difference has to be fetched.

    Only ever an UPGRADE, and only on a positive answer. Anything else - the probe could not
    ask, the credential really is rejected, the API answered something unmapped - leaves the
    outcome exactly as classified, because a record this kernel branches on must not carry a
    classification invented from an absence of evidence.

    A module-level function rather than a method because it needs nothing from the executor
    but the probe, and because that keeps it directly testable without reaching past a
    private name.

    Args:
        outcome: What the dispatch reported.
        probe: Who to ask about the credential.

    Returns:
        ``outcome`` unchanged, or a copy whose error is ``RATE_LIMITED``.

    Example:
        >>> import asyncio
        >>> from agentdag.adapters.kernel.credential_probe import NoCredentialProbe
        >>> from agentdag.domain.models import NodeOutcome, NodeStatus
        >>> done = NodeOutcome(status=NodeStatus.DONE, executor_used="claude",
        ...                    model_used="s", effort_used="-")
        >>> asyncio.run(separated_refusal(done, NoCredentialProbe())) is done
        True
    """
    if outcome.error is None or outcome.error.type is not ErrorType.AUTH_FAILURE:
        return outcome
    finding = await probe.examine()
    if finding.verdict is CredentialVerdict.RATE_LIMITED:
        refused = outcome.error.model_copy(update={"type": ErrorType.RATE_LIMITED})
        return outcome.model_copy(update={"error": refused})
    # The classification stands, but WHY it stands goes into the message. Without this every
    # non-upgrade looks the same in record.json, so a probe broken by a retired model id
    # reads exactly like a healthy timeout and silently restores the defect it exists to
    # fix - the kernel has no log to put this in, the record IS where it says things.
    said = outcome.error.model_copy(update={"message": f"{outcome.error.message} [probe: {finding.detail}]"})
    return outcome.model_copy(update={"error": said})


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
        deny_tools: Tool names refused outright by a ``PreToolUse`` hook
            (:func:`~agentdag.adapters.kernel.hooks_claude.deny_closed_tools`), the same
            request-then-fallback reading as ``deny_bash``. Defaults to EMPTY here because
            the shipped default lives in config (``[kernel] deny_tools``) and reaches this
            field through the composition, never as a second literal that could drift;
            an executor built directly with neither closes no tool.
        tools: The tool set a node may call, matched against
            ``ClaudeAgentOptions.allowed_tools``. Defaults to :data:`DEFAULT_TOOLS`.
        clock: The seam :meth:`_run` reads wall-clock time through to enforce a node's
            own deadline (design 7, M3) - the SAME kind of injected seam every other
            duration in this kernel is measured on (``application.kernel.ports.Clock``),
            never a bare ``time.monotonic()`` call, so a test can drive the deadline
            check with a fake clock instead of a real sleep. Defaults to
            :class:`~agentdag.adapters.kernel.clock_utc.UtcClock` so every call site and
            test fixture built before M3 still constructs without naming it.
        credential_probe: How an auth-shaped failure is checked against the provider
            directly, because the CLI reports quota exhaustion and a rejected credential
            with identical text and a null status field. Defaults to
            :class:`~agentdag.adapters.kernel.credential_probe.NoCredentialProbe`, which
            learns nothing, so an executor built without one classifies exactly as it did
            before probes existed.
    """

    credentials: CredentialSource
    deny_bash: tuple[str, ...] = field(kw_only=True)
    deny_tools: tuple[str, ...] = field(default=(), kw_only=True)
    tools: tuple[str, ...] = field(default=DEFAULT_TOOLS, kw_only=True)
    clock: Clock = field(default_factory=UtcClock, kw_only=True)
    credential_probe: CredentialProbe = field(default_factory=NoCredentialProbe, kw_only=True)

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Validate ``request``, then run it to completion and report its outcome.

        ``request.cwd``/``request.effort`` are checked BEFORE the broad exception
        guard below, not inside it: both a cwd outside the isolation root and an
        unknown effort level are config bugs in whatever BUILT the request, never a
        transient executor failure a retry could fix, so :func:`_cwd_rel` and
        :func:`_validated_effort` raise :class:`~agentdag.domain.kernel_errors.KernelError`
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
            return await separated_refusal(await self._run(request, cwd_rel), self.credential_probe)
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

        The token cap (design 7, M3; ``workflow/design/probes/m3-interrupt.md`` in
        RESEARCH) is enforced HERE, not left to the terminal ``ResultMessage``: once
        :meth:`_on_turn` calls ``client.interrupt()``, ``cap_hit`` is latched and every
        branch below that would otherwise translate the SDK's own report is skipped in
        favour of :meth:`_budget_outcome` - the probe measured that an interrupted
        dispatch's terminal message reports itself as a plain SUCCESS when the interrupt
        landed at a turn boundary and as a transient ``executor_error`` when it landed
        mid-tool, and NEITHER may decide this node's outcome (a plain success would hand
        a half-finished worktree downstream as complete; a transient error would let
        Task 24's retry re-dispatch the same node into spending its cap again). Once
        ``cap_hit`` is set, no further :meth:`_on_turn` call is made even if more
        ``AssistantMessage``s arrive before the stream actually stops - avoids a second,
        redundant ``interrupt()`` call on an already-interrupted client.

        ``running_total`` is this dispatch's cumulative SPEND so far: one entry per API
        REQUEST, :func:`input_total` plus ``output_tokens``, summed. It is accumulated into
        ``spend_by_request`` keyed by ``AssistantMessage.message_id`` rather than added per
        event, because the CLI emits one ``AssistantMessage`` per CONTENT BLOCK and each of
        them repeats the same request's ``message_id`` and the same ``usage``. Adding per
        event therefore charges a request once per block: measured across the stored
        dispatches under the run store, 19 events over 12 distinct ids, and 10/6, 24/16,
        41/23, 26/17 - an inflation of 1.50x to 1.78x, with the distinct-id count equal to
        ``num_turns`` in four of those five. That inflation is what interrupted a node
        holding about 250000 against a 400000 cap and discarded its finished work. A later
        event for a request REPLACES its entry rather than adding to it, so a usage that
        arrives more complete on a subsequent block wins. It is kept here (not inside
        :meth:`_on_turn`, which has no state of its own across calls: this class is a
        frozen dataclass) because it is the loop that owns "one more turn arrived".
        This is the SAME unit :func:`outcome_from_usage` and :meth:`_budget_outcome`
        use to build ``charged_tokens`` (also input_total + output_tokens, just of a
        single terminal usage snapshot rather than summed turn by turn) - see
        :meth:`_on_turn`'s own docstring for why a per-turn figure alone can never
        serve as a spend cap.

        The node deadline (design 7, M3) is checked at the SAME turn seam, right after
        the token cap and only when the cap itself did not already fire this turn (no
        second ``interrupt()`` once one ceiling has already stopped the dispatch) - but
        it is a DIFFERENT quantity entirely: :meth:`_deadline_exceeded` compares WALL-CLOCK
        SECONDS ELAPSED since ``dispatch_started`` (read once, here, before ``query()``)
        against ``request.deadline_s``, never ``running_total`` (a token count) and never
        compared against ``request.token_cap``. ``deadline_hit`` is latched exactly like
        ``cap_hit`` and checked FIRST at both exit points below: a dispatch cannot cross
        both ceilings on the very same turn (only the first ``interrupt()`` call is ever
        made), but if it somehow did, the deadline record - ``cancelled``/``deadline`` -
        is what the run keeps, since a node that ran too long is what actually happened
        even when it also happened to be spending too much.
        """
        # Built with a predicate rather than a flag: the hook is installed before the
        # dispatch starts and armed part-way through it, so it has to read the state at
        # CALL time, from the one object the loop below folds every turn into.
        handover = _Handover()
        options = self._options_for(request, is_stopping=lambda: handover.armed)
        transcript_path = request.node_dir / "transcript.jsonl"
        dispatch_started = self.clock.now()
        first_turn_input = 0
        seen_first_turn = False
        cap_hit = False
        deadline_hit = False
        spend_by_request: dict[str, int] = {}
        running_total = 0
        terminal: ResultMessage | None = None
        async with ClaudeSDKClient(options=options) as client:
            await client.query(request.prompt)
            async for message in client.receive_response():
                append_transcript(transcript_path, message)
                if isinstance(message, AssistantMessage):
                    usage = message.usage or {}
                    if not seen_first_turn:
                        first_turn_input = input_total(usage)
                        seen_first_turn = True
                    # Key by the API request, never by the event: the CLI emits one
                    # AssistantMessage PER CONTENT BLOCK and every one of them repeats that
                    # request's own message_id and usage, so adding per event charges a
                    # request once per block. An event carrying no id cannot be attributed,
                    # so it keeps a key of its own and is counted once, as before.
                    request_key = message.message_id or f"unkeyed-{len(spend_by_request)}"
                    spend_by_request[request_key] = charged_total(usage)
                    running_total = sum(spend_by_request.values())
                    if not cap_hit and not deadline_hit:
                        cap_hit = await self._on_turn(running_total, client, request.token_cap)
                    if (
                        not cap_hit
                        and not deadline_hit
                        and self._deadline_exceeded(dispatch_started, request.deadline_s)
                    ):
                        await client.interrupt()
                        deadline_hit = True
                    # The context ceiling is checked LAST and only when neither hard stop
                    # fired: a node that is out of budget or out of time is stopping for
                    # good, and offering it a successor would hand the chain a way to
                    # outlive the bound that just stopped it. It compares THIS turn's own
                    # context, never the running sum above.
                    if (
                        not cap_hit
                        and not deadline_hit
                        and handover.observe(
                            usage,
                            request.handover_at_tokens,
                            request_id=message.message_id,
                            stop_requested=_subtree_stopping(request),
                        )
                    ):
                        await client.interrupt()
                if isinstance(message, ResultMessage):
                    terminal = message
                    break
        # ONE exit ladder, not one per exit point. The two used to be duplicated and
        # differed only in whether a terminal usage had arrived, which is exactly what
        # `terminal` now carries - so a ceiling added later gets one branch here rather
        # than two that can drift apart.
        usage = (terminal.usage or {}) if terminal is not None else {}
        if deadline_hit:
            return self._deadline_outcome(request, first_turn_input, usage)
        if cap_hit:
            return self._budget_outcome(request, first_turn_input, usage)
        if handover.armed:
            return self._handover_outcome(
                request,
                first_turn_input,
                usage,
                cwd_rel,
                handover.context_at,
                grace_used=handover.grace_used,
                grace_expired=handover.expired,
                stopped_by_subtree=handover.by_subtree,
            )
        if terminal is not None:
            return self._outcome_for(request, terminal, first_turn_input, cwd_rel)
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

    def _options_for(self, request: ExecutorRequest, *, is_stopping: Callable[[], bool]) -> ClaudeAgentOptions:
        """Build this dispatch's SDK options: hooks, credential, a TRUE-allowlisted env, a validated effort.

        Args:
            request: The dispatch to build options for.
            is_stopping: Read on every matched tool use by the stop-notice hook; true once
                the node has crossed its context ceiling and should hand over. A predicate
                rather than a bool because the value changes DURING the dispatch these
                options are already driving.
        """
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
                        hooks=_as_sdk_hooks(
                            deny_outside_write_set(request.isolation_root, allowed=allowed_writes(request))
                        ),
                    ),
                    *self._read_confinement(request),
                    *self._closed_tools(request),
                    # The last hook is not a guard: it decides nothing and blocks nothing,
                    # it puts the stop notice in front of the model once armed. Matched
                    # broadly so the notice reaches a node whatever tool it reaches for.
                    HookMatcher(
                        matcher="Write|Edit|MultiEdit|NotebookEdit|Bash|Read|Grep|Glob",
                        hooks=_as_sdk_hooks(
                            inject_stop_notice(is_stopping, handover_path=str(request.node_dir / HANDOVER_FILENAME))
                        ),
                    ),
                ]
            },
            env=env,
        )

    def _closed_tools(self, request: ExecutorRequest) -> tuple[HookMatcher, ...]:
        """Return the one matcher refusing this request's closed tools, or nothing when the list is empty.

        The request's list wins; the executor's own is the fallback, exactly as for the Bash
        denylist. Empty on both means no matcher, which is what an operator who set
        ``[kernel] deny_tools = []`` asked for.
        """
        names = request.deny_tools or self.deny_tools
        if not names:
            return ()
        return (HookMatcher(matcher="|".join(names), hooks=_as_sdk_hooks(deny_closed_tools())),)

    def _read_confinement(self, request: ExecutorRequest) -> tuple[HookMatcher, ...]:
        """Return the Bash and read matchers for this request, confined or not.

        Two shapes, and which one applies is decided by ``request.read_roots`` alone:

        * ``None`` - reads are unconfined and Bash is filtered by the denylist, which is
          what every node had before confinement existed and what a work node still needs.
        * a tuple - reads are allowlisted to those roots, and Bash is denied OUTRIGHT
          rather than filtered. That pairing is not belt-and-braces: what a shell command
          reads cannot be decided from its text, so leaving Bash on the denylist would
          leave the allowlist meaning nothing. The first real ``plan-goal`` run made every
          one of its excursions through Bash and never touched a path-carrying read tool.

        Args:
            request: The dispatch to build matchers for.

        Returns:
            The matchers to splice into ``PreToolUse``.
        """
        if request.read_roots is None:
            return (
                HookMatcher(
                    matcher="Bash", hooks=_as_sdk_hooks(deny_bash_commands(request.deny_bash or self.deny_bash))
                ),
            )
        return (
            HookMatcher(
                matcher="Bash",
                hooks=_as_sdk_hooks(
                    deny_every_bash_command(
                        "this node's reads are confined to its own directory and working directory, "
                        "and a shell command's reads cannot be checked against that, so Bash is refused. "
                        "Everything you need is in your prompt and your brief; write your output file "
                        "with Write, and read your own directory with Read, Grep or Glob."
                    )
                ),
            ),
            HookMatcher(matcher="Read|Grep|Glob", hooks=_as_sdk_hooks(deny_reads_outside(request.read_roots))),
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
            subtype=message.subtype or "",
        )
        if request.effort:
            # Safe to stamp the REQUESTED value here specifically: _options_for already
            # validated it (via _validated_effort) with no raise, earlier in this same
            # dispatch, so the SDK call this ResultMessage came from actually carried it.
            outcome = outcome.model_copy(update={"effort_used": request.effort})
        return outcome

    def _handover_outcome(
        self,
        request: ExecutorRequest,
        first_turn_input: int,
        usage: Mapping[str, Any],
        cwd_rel: str,
        context_at_handover: int,
        *,
        grace_used: int,
        grace_expired: bool,
        stopped_by_subtree: bool,
    ) -> NodeOutcome:
        """Build the record a node gets when its CONTEXT ceiling stopped it (design 3.8).

        The one stopped-dispatch record that is NOT a failure, and the one that KEEPS its
        artefact ref. :meth:`_budget_outcome` and :meth:`_deadline_outcome` both empty
        ``artefact_refs`` deliberately, so a half-finished worktree is never presented as a
        completed one - the right call when the node is stopping for good. A handover is
        the opposite case: the work in that tree is exactly what the successor continues
        from, so dropping the ref would throw away the thing the mechanism exists to save.

        ``status`` is ``NEEDS_CONTINUATION`` and ``error`` is ``None``: crossing a context
        ceiling is a scheduled event, not something that went wrong, and a caller
        branching on ``error is not None`` must not see this as a fault.

        Args:
            request: The dispatch's request; names the model row.
            first_turn_input: This dispatch's first turn's context, for ``key_facts``.
            usage: The terminal usage if one arrived, else ``{}``.
            cwd_rel: The node's working directory relative to the isolation root - the
                artefact ref the successor reads.
            context_at_handover: The turn context that passed the ceiling, recorded so a
                drift signal (design 3.5) can be read off the record.
            grace_used: How many API requests the node spent after being asked to hand over.
            grace_expired: Whether the grace ran out and the dispatch was interrupted, as
                against the node stopping of its own accord. Both end NEEDS_CONTINUATION
                carrying the same ceiling figures, so without this the record cannot say
                which happened - and that distinction is what decides design 3.8's
                expiry behaviour. A live run measured 6 armed dispatches, 2 of them exactly
                at the threshold, and could classify none of them
                (RESEARCH ``workflow/design/probes/live-handover.md``).

        Returns:
            A ``NEEDS_CONTINUATION`` outcome carrying the worktree and the trigger figure.
        """
        in_tokens = input_total(usage)
        out_tokens = int(usage.get("output_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))
        tokens = Tokens(**{"in": in_tokens, "out": out_tokens, "cache_read": cache_read, "reasoning": None})
        return NodeOutcome(
            status=NodeStatus.NEEDS_CONTINUATION,
            artefact_refs=[cwd_rel],
            key_facts={
                "context_at_handover": context_at_handover,
                "handover_at_tokens": request.handover_at_tokens,
                "first_turn_input_tokens": first_turn_input,
                "grace_used": grace_used,
                "grace_expired": grace_expired,
                "stopped_by_subtree": stopped_by_subtree,
            },
            # `grace_expired` is TYPED, by direct analogy with `cap_hit` and `deadline_hit`:
            # it is this outcome's decisive fact, and design 3.3 lets the coordinator branch
            # ONLY on a key named here. `grace_used` stays free text - a measurement, like
            # `first_turn_input_tokens`, not something a branch should read.
            typed_fields=["context_at_handover", "grace_expired", "stopped_by_subtree"],
            tokens=tokens,
            charged_tokens={request.model: charged_total(usage)},
            executor_used="claude",
            model_used=request.model,
            effort_used=_NO_VALUE,
            error=None,  # a handover is a scheduled event, never a fault
        )

    async def _on_turn(self, running_total: int, client: _Interruptible, cap: int | None) -> bool:
        """Per-``AssistantMessage`` hook point: stop the dispatch once its RUNNING SPEND passes ``cap``.

        Compares ``running_total`` - :meth:`_run`'s running sum of every turn's own
        :func:`input_total` plus ``output_tokens`` seen so far this dispatch - against
        ``cap``. Two different readings of a turn's usage exist and this cap needs the
        SUM, never a single turn's own figure alone:

        * A single ``AssistantMessage.usage``'s own :func:`input_total` is the
          CONTEXT SIZE at that turn - "what the model just saw" (design 3.8) - bounded
          by the model's context window. That figure belongs to design 3.8's separate,
          later context-ceiling mechanism (``handover_at_tokens``), which will read a
          turn's own ``input_total`` directly and NOT sum it, because a context ceiling
          asks "is the window full right now", a question a running sum cannot answer
          (it would trip on a long dispatch whose window is nowhere near full).
        * A per-node cap, in contrast, is a SPEND budget, and spend is charged per
          request: every turn re-charges its whole input again (prompt caching
          discounts the cost of a cache read, not the TOKEN COUNT this module sums), so
          a dispatch's true spend is the SUM across turns - exactly what the terminal
          ``ResultMessage.usage`` already reports and what :func:`outcome_from_usage`
          and :meth:`_budget_outcome` record as ``charged_tokens``. Comparing a single
          turn's context size against a spend cap has no usable threshold: set the cap
          near what a node actually spends and no single turn's context ever reaches
          it (the node runs to completion, unbounded); set it below one turn's context
          and every node dies on its first turn. Measured on the M2 attended runs,
          from the run records themselves rather than a design probe doc (a run's
          ``nodes/<node_id>/<hash>/record.json`` under ``/var/lib/agentdag/runs/<run-id>/`` -
          run ``20260818T060025Z-21e810``, node ``w_migrate@1``, hash ``b47149d9``): a
          28-turn dispatch charged 802,098 tokens total while its first turn alone was
          26,029 - far more than any single turn's context, and only explainable as a sum.

        Args:
            running_total: This dispatch's cumulative spend so far, INCLUDING the turn
                that just arrived - see :meth:`_run`.
            client: The live client this dispatch is running on - needed to call
                ``interrupt()``; M2's docstring on this method already flagged the
                signature would have to change for exactly this.
            cap: ``request.token_cap`` - this node's own cap for the resolved row, or
                ``None`` when the node declares no cap for it, in which case nothing is
                enforced (mirrors :meth:`~agentdag.application.kernel.context.Coordinator._run_cap_refusal`'s
                same "no cap declared, nothing checked" rule on the run-level call site,
                and the same SPEND unit - see that method's docstring). The comparison
                is INCLUSIVE of ``cap`` itself: ``running_total == cap`` does NOT
                interrupt, only ``running_total > cap`` does - a node's cap is a ceiling
                it may fully spend, not a strict bound that trips on reaching it exactly
                (the code below reads ``running_total <= cap: return False``).

        Returns:
            Whether ``running_total`` passed ``cap`` and ``client.interrupt()`` was
            called. :meth:`_run` uses this to stamp the record ``BUDGET_EXCEEDED``
            itself, regardless of which of the two shapes the (now-interrupted)
            dispatch's own terminal message reports - the probe measured that message
            never says "interrupted" and gets it backwards in both directions.
        """
        if cap is None or running_total <= cap:
            return False
        await client.interrupt()
        return True

    def _budget_outcome(self, request: ExecutorRequest, first_turn_input: int, usage: Mapping[str, Any]) -> NodeOutcome:
        """Build the record a cap-stopped dispatch gets, stamped on the path that called ``interrupt()``.

        This is the ONLY place that gets to decide a capped node's outcome - never the
        terminal ``ResultMessage`` :meth:`_run` may still receive afterward (the probe
        measured one always arrives, carrying real usage, which is why ``usage`` is
        threaded through here rather than left at zero: a capped node still spent real
        tokens and :attr:`~agentdag.application.kernel.context.Coordinator.tokens_by_row`
        must reflect that, the same as any other outcome's ``charged_tokens``).

        Two things this outcome deliberately does NOT carry, both load-bearing:
        ``artefact_refs`` stays empty (never ``[cwd_rel]``) so a work node's
        half-finished worktree is never handed downstream as a completed artefact - the
        empty-result refusal (:func:`~agentdag.application.kernel.dispatch._refuse_empty`)
        only inspects a ``DONE`` outcome, so it cannot rescue this one; and
        ``error.transient`` is ``False``, so Task 24's retry path never re-dispatches a
        node that was stopped for spending its own budget, straight back into spending
        it again.

        Args:
            request: The dispatch this outcome is for - ``request.model`` names the row
                charged, ``request.token_cap`` is quoted in the error message.
            first_turn_input: What :meth:`_run` recorded as the FIRST turn's own
                :func:`input_total`, kept in ``key_facts`` like every other outcome.
            usage: The terminal ``ResultMessage.usage`` the interrupted dispatch still
                produced, or ``{}`` on the rarer path where the stream ended with no
                terminal message at all.

        Returns:
            A ``FAILED`` outcome, ``error.type=BUDGET_EXCEEDED``, ``transient=False``,
            ``key_facts["cap_hit"] = True``, tokens/``charged_tokens`` from ``usage``.
        """
        in_tokens = input_total(usage)
        out_tokens = int(usage.get("output_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))
        tokens = Tokens(**{"in": in_tokens, "out": out_tokens, "cache_read": cache_read, "reasoning": None})
        return NodeOutcome(
            status=NodeStatus.FAILED,
            key_facts={"cap_hit": True, "first_turn_input_tokens": first_turn_input},
            typed_fields=["cap_hit"],
            tokens=tokens,
            charged_tokens={request.model: charged_total(usage)},
            executor_used="claude",
            model_used=request.model,
            effort_used=_NO_VALUE,
            error=NodeError(
                type=ErrorType.BUDGET_EXCEEDED,
                message=(
                    f"node token cap {request.token_cap} exceeded at a turn seam; "
                    "interrupted, overshoot bounded by one turn"
                ),
                transient=False,
            ),
        )

    def _deadline_exceeded(self, dispatch_started: datetime, deadline_s: float | None) -> bool:
        """Whether WALL-CLOCK time elapsed since ``dispatch_started`` has passed ``deadline_s``.

        The node-deadline half of the M3 turn seam, deliberately a PURE comparison with
        no ``interrupt()`` call of its own (unlike :meth:`_on_turn`, which both decides
        AND acts) - :meth:`_run` calls ``client.interrupt()`` itself once this returns
        ``True``, the same shape :meth:`_on_turn` uses, kept as two separate calls here
        only because this method has no client to call it on without widening its
        signature for no reason: ``self.clock.now() - dispatch_started`` is the only
        thing it needs.

        Args:
            dispatch_started: When this dispatch's ``query()`` call was made, read from
                :attr:`clock` ONCE at the top of :meth:`_run` - never re-read here, so
                every turn's check measures elapsed time against the SAME start.
            deadline_s: ``request.deadline_s`` - this node's own wall-clock ceiling in
                SECONDS, already clamped to ``Policy.deadline_ceiling_s`` by
                :meth:`~agentdag.application.kernel.context.Coordinator.work` before it
                ever reached :class:`~agentdag.application.kernel.ports.ExecutorRequest`,
                or ``None`` for a call site that predates this field (every test fixture
                built before M3) - nothing is enforced then, mirroring :meth:`_on_turn`'s
                own "no cap declared, nothing checked" rule for :attr:`token_cap`.

        Returns:
            ``True`` once elapsed SECONDS strictly exceeds ``deadline_s`` - inclusive at
            the boundary itself, same as :meth:`_on_turn`'s own ``<=`` reading of
            ``token_cap``: a node may fully spend the deadline it was given, not be cut
            off the instant it reaches it exactly.

        Example:
            >>> from datetime import datetime, timedelta, timezone
            >>> from pathlib import Path
            >>> class _FixedClock:
            ...     def __init__(self, now: datetime) -> None:
            ...         self._now = now
            ...     def now(self) -> datetime:
            ...         return self._now
            >>> started = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)
            >>> executor = ClaudeExecutor(
            ...     OAuthTokenFile(Path("unused")), deny_bash=(),
            ...     clock=_FixedClock(started + timedelta(seconds=10)),
            ... )
            >>> executor._deadline_exceeded(started, 10.0)
            False
            >>> executor._deadline_exceeded(started, 9.0)
            True
        """
        if deadline_s is None:
            return False
        elapsed_s = (self.clock.now() - dispatch_started).total_seconds()
        return elapsed_s > deadline_s

    def _deadline_outcome(
        self, request: ExecutorRequest, first_turn_input: int, usage: Mapping[str, Any]
    ) -> NodeOutcome:
        """Build the record a deadline-stopped dispatch gets, stamped on the path that called ``interrupt()``.

        Mirrors :meth:`_budget_outcome` exactly - the SAME probe finding applies here:
        an interrupted dispatch's terminal message never says "interrupted" (a plain
        SUCCESS at a turn boundary, a transient ``executor_error`` mid-tool), so this is
        the ONLY place a deadline-stopped node's outcome may be decided, never the
        terminal ``ResultMessage`` :meth:`_run` may still receive afterward. Same two
        load-bearing omissions too: no ``artefact_refs`` (a half-finished worktree is
        never handed downstream as complete) and ``error.transient=False`` (a node
        stopped for running too long must not be Task 24's retry target, straight back
        into running out of time again).

        Args:
            request: The dispatch this outcome is for - ``request.model`` names the row
                charged, ``request.deadline_s`` is quoted in the error message.
            first_turn_input: What :meth:`_run` recorded as the FIRST turn's own
                :func:`input_total`, kept in ``key_facts`` like every other outcome.
            usage: The terminal ``ResultMessage.usage`` the interrupted dispatch still
                produced, or ``{}`` on the rarer path where the stream ended with no
                terminal message at all.

        Returns:
            A ``CANCELLED`` outcome (design 2.2: "cancelled: the scheduler stopped it -
            deadline or cancel"), ``error.type=DEADLINE``, ``transient=False``,
            ``key_facts["deadline_hit"] = True``, tokens/``charged_tokens`` from ``usage``.
        """
        in_tokens = input_total(usage)
        out_tokens = int(usage.get("output_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))
        tokens = Tokens(**{"in": in_tokens, "out": out_tokens, "cache_read": cache_read, "reasoning": None})
        return NodeOutcome(
            status=NodeStatus.CANCELLED,
            key_facts={"deadline_hit": True, "first_turn_input_tokens": first_turn_input},
            typed_fields=["deadline_hit"],
            tokens=tokens,
            charged_tokens={request.model: charged_total(usage)},
            executor_used="claude",
            model_used=request.model,
            effort_used=_NO_VALUE,
            error=NodeError(
                type=ErrorType.DEADLINE,
                message=(
                    f"node deadline {request.deadline_s}s exceeded at a turn seam; "
                    "interrupted, overshoot bounded by one turn"
                ),
                transient=False,
            ),
        )


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
