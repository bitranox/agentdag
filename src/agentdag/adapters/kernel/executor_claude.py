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

The child environment is an ALLOWLIST, never the coordinator's own (:func:`child_env`
on each :class:`CredentialSource`), plus an explicit empty override for every OTHER
inherited variable that looks like a secret (:func:`_secret_shaped_overrides`) - two
layers, because the SDK MERGES its ``env`` argument OVER ``os.environ`` (M1 note)
rather than replacing it, so omitting a key lets the merge read it straight from the
coordinator's own process. The credential is minted per-node from one of two sources
(D3, RESEARCH): :class:`OAuthTokenFile` (the measured default: D3 arm A2 proved
``CLAUDE_CODE_OAUTH_TOKEN`` authenticates a child whose ``CLAUDE_CONFIG_DIR`` is
empty) or :class:`CredentialCopy` (D3 arm C: a private, owner-only copy of the
operator's ``.credentials.json``, mirroring M1's ``_copy_credential``).

Every streamed message is scrubbed (:func:`scrub`) and appended to
``node_dir/transcript.jsonl`` as it arrives - a secret-shaped VALUE never reaches
disk even transiently. :func:`outcome_from_usage` is the pure translation from one
dispatch's terminal usage to the typed :class:`~agentdag.domain.models.NodeOutcome`
the coordinator branches on; it is unit-tested with no SDK call at all
(``tests/test_kernel_executor_claude.py``).

Contents:
    * :data:`DEFAULT_TOOLS` - the tool set a node gets when the caller does not override it.
    * :class:`CredentialSource` - what :class:`ClaudeExecutor` needs from a credential.
    * :class:`OAuthTokenFile` - a per-operator OAuth-token keyfile (the measured default).
    * :class:`CredentialCopy` - a private, owner-only copy of ``.credentials.json``.
    * :func:`scrub` - recursively redact secret-shaped dict values.
    * :func:`outcome_from_usage` - pure: one dispatch's terminal usage -> :class:`NodeOutcome`.
    * :class:`ClaudeExecutor` - the :class:`~agentdag.application.kernel.ports.Executor` implementation.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage

from ...domain.models import ErrorType, NodeError, NodeOutcome, NodeStatus, Tokens
from .hooks_claude import deny_bash_commands, deny_outside_root

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from claude_agent_sdk import HookCallback as _SdkHookCallback

    from ...application.kernel.ports import ExecutorRequest

__all__ = [
    "DEFAULT_TOOLS",
    "ClaudeExecutor",
    "CredentialCopy",
    "CredentialSource",
    "OAuthTokenFile",
    "outcome_from_usage",
    "scrub",
]

DEFAULT_TOOLS = ("Read", "Edit", "Write", "Bash", "Grep", "Glob")
"""The tool set a node gets when :class:`ClaudeExecutor` is built with no override (M1's ``_TOOLS``)."""

_CONFIG_DIR_NAME = ".claude"
_CREDENTIALS_NAME = ".credentials.json"
_OWNER_ONLY = 0o600
_NO_VALUE = "-"
"""The sentinel :mod:`.dispatch` and this module both use for a field with no real value."""

# The allowlist child_env() builds the coordinator's PATH/LANG/... from - never the whole
# environment. api_key/apikey are not part of this list: they name what a node's OWN
# credential is called, not a variable this adapter ever forwards unnamed.
_ALLOWLIST_KEYS = ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "USERPROFILE")

# child_env()'s own leak-blanking layer: every OTHER inherited variable whose name looks
# like a secret, forced to "". Named exactly as the brief's GREEN prose gives it
# (api_key/apikey included) - a DIFFERENT, narrower pattern from _SECRET_VALUE_RE below,
# which redacts DICT VALUES inside a streamed message, not environment variable NAMES.
_SECRET_ENV_RE = re.compile(r"(?i)token|secret|password|authorization|credential|api_key|apikey")

# scrub()'s pattern, exactly as the brief's Interfaces section gives it for the
# transcript scrubber - deliberately not the same regex as _SECRET_ENV_RE above.
_SECRET_VALUE_RE = re.compile(r"(?i)token|secret|password|authorization|credential")

_AUTH_FAILURE_TEXT = "Not logged in"


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


def _secret_shaped_overrides() -> dict[str, str]:
    """Every inherited variable whose name looks like a secret, forced to ``""``.

    The SDK merges its own ``env`` OVER ``os.environ`` (M1 note) rather than
    replacing it, so the only way this adapter can blank a secret-shaped variable the
    COORDINATOR process itself inherited is an explicit empty override here - omitting
    the key lets the merge read it straight from the coordinator's own environment.
    This is the SECOND of two layers; the FIRST is that the coordinator process itself
    must be started with a clean environment (Task 17), so a leak needs both to fail.
    Applied UNDER a :class:`CredentialSource`'s own env in :meth:`ClaudeExecutor.run`,
    so a real credential value (its own name usually matches this same pattern) always
    wins over the blank default rather than being blanked by it.
    """
    return {key: "" for key in os.environ if _SECRET_ENV_RE.search(key)}


def _home_and_config_dir(node_dir: Path) -> tuple[Path, Path]:
    """Create and return ``(node_dir/home, node_dir/home/.claude)``."""
    home = node_dir / "home"
    config_dir = home / _CONFIG_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
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
        handle = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _OWNER_ONLY)
    except FileExistsError:
        return
    with os.fdopen(handle, "wb") as opened:
        opened.write(payload)


def scrub(value: Any) -> Any:
    """Recursively replace a secret-shaped dict VALUE with ``"[scrubbed]"``.

    A value is replaced when its own KEY matches :data:`_SECRET_VALUE_RE`
    (``(?i)token|secret|password|authorization|credential``); every other value is
    recursed into (a list element, or a value under a non-matching key) so a secret
    nested deeper in the structure is still caught. Used on every message this
    module streams to ``transcript.jsonl`` (:meth:`ClaudeExecutor.run`) and directly
    by ``tests/test_kernel_secrets.py`` to prove the redaction is real, not vacuous.

    Args:
        value: A JSON-shaped value - typically one streamed SDK message rendered to a
            dict - about to be written to disk.

    Returns:
        A structurally identical copy with every secret-shaped value redacted.

    Example:
        >>> scrub({"tool_input": {"password": "hunter2", "note": "ok"}})
        {'tool_input': {'password': '[scrubbed]', 'note': 'ok'}}
    """
    if isinstance(value, dict):
        mapping = cast("dict[Any, Any]", value)
        return {key: "[scrubbed]" if _SECRET_VALUE_RE.search(str(key)) else scrub(val) for key, val in mapping.items()}
    if isinstance(value, list):
        elements = cast("list[Any]", value)
        return [scrub(item) for item in elements]
    return value


def _classify_error(text: str) -> NodeError:
    """Classify a failed dispatch's result text: the CLI's own login failure, or a generic executor error."""
    if _AUTH_FAILURE_TEXT in text:
        return NodeError(type=ErrorType.AUTH_FAILURE, message=text, transient=False)
    return NodeError(type=ErrorType.EXECUTOR_ERROR, message=text, transient=True)


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
    value, when the request carries one, is folded in by :meth:`ClaudeExecutor.run`
    (this function's signature has no ``effort`` parameter, per the brief it implements).

    Args:
        model: The model row the dispatch ran under; keys ``charged_tokens`` and
            stamps ``model_used``.
        num_turns: ``ResultMessage.num_turns``.
        is_error: ``ResultMessage.is_error``.
        text: ``ResultMessage.result`` (or ``""`` when the SDK reported none) - scanned
            for the CLI's own "Not logged in" text to name an auth failure specifically.
        usage: ``ResultMessage.usage`` (or ``{}``); only the four cache/token fields
            are read, each defaulting to 0 if this SDK version does not emit it.
        first_turn_input: The FIRST ``AssistantMessage.usage["input_tokens"]`` this
            dispatch saw, recorded by :meth:`ClaudeExecutor.run` as it streams - a
            distinct figure from the terminal ``usage`` (the LAST turn's), kept in
            ``key_facts`` for M3's per-turn spend cap.
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
    in_tokens = (
        int(usage.get("input_tokens", 0))
        + int(usage.get("cache_creation_input_tokens", 0))
        + int(usage.get("cache_read_input_tokens", 0))
    )
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
    (so it is unit-testable with a synthetic dict and no SDK types at all), while the
    SDK's own ``HookCallback`` types it as ``HookInput`` - a union of TypedDicts. pyright
    treats a TypedDict as NOT assignable to ``dict[str, Any]`` (invariant, mutable), so
    the two callable shapes are not STRUCTURALLY compatible even though every concrete
    value the SDK ever passes at runtime - a TypedDict IS a dict - satisfies the wider
    parameter type just fine (the M2 probe, ``workflow/design/probes/m2-hooks-dontask.md``
    in RESEARCH, measured exactly this: the SDK invokes both hooks and respects their
    decision). This cast documents that the mismatch is a Python type-system limitation
    at this boundary, not a real behavioural gap.

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
        """Run ``request`` to completion and report its outcome.

        Any exception - the model call is the one genuinely external edge here (M1's
        docstring) - becomes a FAILED outcome with ``executor_error`` (transient),
        never past this method: :mod:`.dispatch`'s own ``Exception`` guard is a second,
        outer layer; this one is the first, and the only place that can report the
        MORE SPECIFIC ``auth_failure`` when the SDK itself completes but reports one.
        """
        try:
            return await self._run(request)
        except Exception as exc:  # the external edge: never let it past this node
            return NodeOutcome(
                status=NodeStatus.FAILED,
                executor_used="claude",
                model_used=request.model,
                effort_used=request.effort or _NO_VALUE,
                error=NodeError(type=ErrorType.EXECUTOR_ERROR, message=f"{type(exc).__name__}: {exc}", transient=True),
            )

    async def _run(self, request: ExecutorRequest) -> NodeOutcome:
        """Do the real work of :meth:`run`, inside its exception guard."""
        options = self._options_for(request)
        transcript_path = request.node_dir / "transcript.jsonl"
        first_turn_input = 0
        seen_first_turn = False
        async with ClaudeSDKClient(options=options) as client:
            await client.query(request.prompt)
            async for message in client.receive_response():
                _append_transcript(transcript_path, message)
                if isinstance(message, AssistantMessage):
                    usage = message.usage or {}
                    self._on_turn(usage)
                    if not seen_first_turn:
                        first_turn_input = int(usage.get("input_tokens", 0))
                        seen_first_turn = True
                if isinstance(message, ResultMessage):
                    return self._outcome_for(request, message, first_turn_input)
        return NodeOutcome(
            status=NodeStatus.FAILED,
            executor_used="claude",
            model_used=request.model,
            effort_used=request.effort or _NO_VALUE,
            error=NodeError(type=ErrorType.EXECUTOR_ERROR, message="no ResultMessage", transient=True),
        )

    def _options_for(self, request: ExecutorRequest) -> ClaudeAgentOptions:
        """Build this dispatch's SDK options: hooks, credential, allowlisted env."""
        env = {**_secret_shaped_overrides(), **self.credentials.child_env(request.node_dir)}
        return ClaudeAgentOptions(
            cwd=str(request.cwd),
            system_prompt=request.brief,
            setting_sources=[],
            model=request.model,
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

    def _outcome_for(self, request: ExecutorRequest, message: ResultMessage, first_turn_input: int) -> NodeOutcome:
        """Translate a terminal :class:`ResultMessage` via :func:`outcome_from_usage`, effort folded in."""
        cwd_rel = request.cwd.relative_to(request.isolation_root).as_posix()
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
            outcome = outcome.model_copy(update={"effort_used": request.effort})
        return outcome

    def _on_turn(self, usage: dict[str, Any]) -> None:
        """Per-``AssistantMessage`` hook point; a no-op here.

        M3 fills this in with the per-turn spend check and ``client.interrupt()`` call
        (design 7's "the token cap has two call sites"); this task only creates the
        seam and records ``first_turn_input_tokens`` (done by :meth:`_run` itself, not
        by this method).
        """


def _append_transcript(path: Path, message: object) -> None:
    """Scrub and append one streamed SDK message to ``path`` as a single JSON line."""
    line = scrub(_message_to_jsonable(message))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, default=str))
        handle.write("\n")
