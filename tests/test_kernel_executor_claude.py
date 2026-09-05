"""RED/GREEN tests for the Claude kernel executor: hooks, env allowlist, usage translation.

No SDK process is started here - every assertion is a synthetic hook-input dict, a
temp filesystem, or a hand-built ``usage`` mapping. The SDK is the one genuinely
external edge (M1's own docstring for its adapter says the same); it is exercised by
the M2 probe (``workflow/design/probes/m2-hooks-dontask.md`` in RESEARCH) instead. A
few tests below DO drive ``ClaudeExecutor.run()``/``_options_for()`` for real, but only
through paths that fail BEFORE any SDK/network call (an invalid effort, a validated
env build) - see each test's own docstring for why it stays network-free.
"""

from __future__ import annotations

import asyncio
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage

# append_transcript and input_total are tested seams this fix round's review asked to
# be unit-tested directly (owner-only file mode; the three-field sum) rather than only
# indirectly through ClaudeExecutor.run(), which would need a real SDK/network call
# this module's own design keeps out of unit tests - both are public, per the same
# review, exactly because they are tested this way.
from agentdag.adapters.kernel import executor_claude as executor_claude_module
from agentdag.adapters.kernel.executor_claude import (
    HANDOVER_GRACE_TURNS,
    ClaudeExecutor,
    CredentialCopy,
    OAuthTokenFile,
    allowed_writes,
    append_transcript,
    input_total,
    outcome_from_usage,
)
from agentdag.adapters.kernel.hooks_claude import (
    HookCallback,
    deny_bash_commands,
    deny_every_bash_command,
    deny_outside_write_set,
    deny_reads_outside,
    inject_stop_notice,
)
from agentdag.application.kernel.ports import ExecutorRequest
from agentdag.domain.handover import HANDOVER_FILENAME
from agentdag.domain.kernel_errors import KernelError
from agentdag.domain.models import NodeStatus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence


def decision(result: dict[str, Any]) -> str | None:
    specific: dict[str, Any] = result.get("hookSpecificOutput") or {}
    return specific.get("permissionDecision")


async def _await_hook(hook: HookCallback, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Await one hook call as a coroutine.

    A hook is typed as returning an ``Awaitable``; ``asyncio.run`` accepts a bare awaitable
    only from Python 3.14 on, so the call is wrapped for the older interpreters CI runs.
    """
    return dict(await hook({"tool_name": tool_name, "tool_input": tool_input}, None, None))


def fire(hook: HookCallback, tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Run one hook call the way the SDK would, and return its permission decision."""
    return decision(asyncio.run(_await_hook(hook, tool_name, tool_input)))


def _only_hook(matchers: Sequence[HookMatcher], matcher: str) -> HookCallback:
    """Return the single hook wired to ``matcher``, so a test can fire it.

    Selecting by matcher string and requiring exactly one keeps the assertion about what
    a matcher DOES: a test that indexed positionally would keep passing if two matchers
    swapped, which is the mistake worth catching here.
    """
    found = [m for m in matchers if m.matcher == matcher]
    assert len(found) == 1, f"expected one {matcher!r} matcher, got {len(found)}"
    hooks = found[0].hooks
    assert len(hooks) == 1, f"expected one hook on {matcher!r}, got {len(hooks)}"
    return cast("HookCallback", hooks[0])


def _pretooluse(options: ClaudeAgentOptions) -> list[HookMatcher]:
    """Return the PreToolUse matchers of built options, failing plainly if none were registered."""
    assert options.hooks is not None, "options carry no hooks at all"
    return options.hooks["PreToolUse"]


def _request(tmp_path: Path, **overrides: Any) -> ExecutorRequest:
    """Build a minimal, well-formed ``ExecutorRequest`` under ``tmp_path`` for a unit test.

    ``node_dir`` is created on disk (production's ``FsRunDir.node_dir`` always creates
    it before a body runs, and ``child_env`` needs it to exist to create ``home/`` under
    it); ``cwd`` is a real directory under ``isolation_root`` so ``_cwd_rel`` succeeds
    by default - a test that wants the failure path overrides ``cwd`` or ``effort``.
    """
    isolation_root = tmp_path / "run"
    node_dir = isolation_root / "nodes" / "n1"
    node_dir.mkdir(parents=True, exist_ok=True)
    cwd = isolation_root / "wt" / "r"
    cwd.mkdir(parents=True, exist_ok=True)
    fields: dict[str, Any] = {
        "node_dir": node_dir,
        "cwd": cwd,
        "brief": "do the thing",
        "prompt": "do the thing",
        "model": "sonnet",
        "effort": None,
        "max_turns": 5,
        "isolation_root": isolation_root,
        "write_set": (),
        "deny_bash": (),
    }
    fields.update(overrides)
    return ExecutorRequest(**fields)


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="symlink_to needs elevated privileges on Windows")
def test_write_hook_denies_paths_outside_the_isolation_root_after_realpath(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "wt").mkdir()
    outside = tmp_path / "elsewhere.txt"
    (root / "wt" / "link").symlink_to(tmp_path)  # the symlink route out
    hook = deny_outside_write_set(root, allowed=("wt/**",))
    assert fire(hook, "Write", {"file_path": str(root / "wt" / "a.py")}) is None
    assert fire(hook, "Write", {"file_path": str(outside)}) == "deny"
    assert fire(hook, "Edit", {"file_path": str(root / "wt" / "link" / "x")}) == "deny"
    assert fire(hook, "Write", {"file_path": str(root / "wt" / ".." / ".." / "escape")}) == "deny"


@pytest.mark.os_agnostic
def test_write_hook_handles_notebookedit_and_fails_closed_with_no_path_key(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "wt").mkdir()
    hook = deny_outside_write_set(root, allowed=("wt/**",))
    assert fire(hook, "NotebookEdit", {"notebook_path": str(root / "wt" / "nb.ipynb")}) is None
    assert fire(hook, "NotebookEdit", {"notebook_path": str(tmp_path / "elsewhere.ipynb")}) == "deny"
    # A matched tool (Write/Edit/MultiEdit/NotebookEdit) whose input carries NEITHER
    # "file_path" nor "notebook_path" must fail CLOSED, not silently pass through -
    # this hook is the only thing standing between the tool and an unrestricted write.
    assert fire(hook, "Write", {}) == "deny"
    assert fire(hook, "NotebookEdit", {"cell_id": "abc"}) == "deny"


@pytest.mark.os_agnostic
def test_bash_hook_denies_the_listed_commands_however_spaced() -> None:
    hook = deny_bash_commands(("git push", "gh pr"))
    assert fire(hook, "Bash", {"command": "git   push origin main"}) == "deny"
    assert fire(hook, "Bash", {"command": "git status && gh  pr create"}) == "deny"
    assert fire(hook, "Bash", {"command": "git commit -m x"}) is None


@pytest.mark.os_agnostic
def test_child_env_is_an_allowlist_and_the_credential_never_touches_the_operator_file(tmp_path: Path) -> None:
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    node_dir = tmp_path / "node"
    node_dir.mkdir()
    env = OAuthTokenFile(keyfile).child_env(node_dir)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-SECRET"
    assert Path(env["CLAUDE_CONFIG_DIR"]).is_dir()
    assert not any(Path(env["CLAUDE_CONFIG_DIR"]).iterdir())
    assert set(env) <= {
        "HOME",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "REMEMBER_PROMPT_STAMP",
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
        "USERPROFILE",
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
        "APPDATA",
        "LOCALAPPDATA",
        "WINDIR",
        "SYSTEMDRIVE",
        "PROGRAMDATA",
        "HOMEDRIVE",
        "HOMEPATH",
    }
    src = tmp_path / "creds.json"
    src.write_text('{"t": 1}')
    env2 = CredentialCopy(src).child_env(tmp_path / "node2")
    copy = Path(env2["CLAUDE_CONFIG_DIR"]) / ".credentials.json"
    assert copy.read_text() == '{"t": 1}'
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env2
    copy.write_text("refreshed")
    assert src.read_text() == '{"t": 1}'


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits are not meaningful on Windows")
def test_child_env_creates_home_and_config_dir_owner_only(tmp_path: Path) -> None:
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    node_dir = tmp_path / "node"
    node_dir.mkdir()
    env = OAuthTokenFile(keyfile).child_env(node_dir)
    home_mode = stat.S_IMODE(Path(env["HOME"]).stat().st_mode)
    config_mode = stat.S_IMODE(Path(env["CLAUDE_CONFIG_DIR"]).stat().st_mode)
    assert home_mode == 0o700
    assert config_mode == 0o700


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits are not meaningful on Windows")
def test_append_transcript_writes_the_file_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    append_transcript(path, {"note": "hello"})
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert "hello" in path.read_text(encoding="utf-8")


@pytest.mark.os_agnostic
def test_input_total_sums_the_three_input_fields() -> None:
    assert (
        input_total({"input_tokens": 50, "cache_creation_input_tokens": 3873, "cache_read_input_tokens": 119786})
        == 50 + 3873 + 119786
    )
    assert input_total({}) == 0
    assert input_total({"input_tokens": 7}) == 7


@pytest.mark.os_agnostic
def test_options_for_passes_a_validated_effort_and_rejects_an_unknown_one(tmp_path: Path) -> None:
    """``_options_for`` never touches the network, so this is a real-wiring unit test.

    Stays on the private ``_options_for`` (not the public ``build_options_env``,
    which only builds the env, not a full ``ClaudeAgentOptions``) because checking
    ``options.effort`` needs the real options object; both ignores are narrow and
    would go away only if ``_options_for`` itself became public or its effort value
    got hoisted out alongside the env in ``build_options_env``.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    options = executor._options_for(  # pyright: ignore[reportPrivateUsage]
        _request(tmp_path, effort="high"), is_stopping=lambda: False
    )
    assert options.effort == "high"
    with pytest.raises(KernelError):
        executor._options_for(  # pyright: ignore[reportPrivateUsage]
            _request(tmp_path, effort="bogus"), is_stopping=lambda: False
        )


@pytest.mark.os_agnostic
def test_run_raises_kernel_error_for_an_unknown_effort_before_any_dispatch(tmp_path: Path) -> None:
    """An invalid effort is a config bug, validated by ``_validated_effort`` in
    ``run()`` itself, BEFORE its own broad except-Exception guard - it must propagate
    out of ``run()``, never come back as a FAILED/``executor_error`` record ``run()``
    itself produced (a retrier would just try the identical bad request again).
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    request = _request(tmp_path, effort="turbo")
    with pytest.raises(KernelError, match="turbo"):
        asyncio.run(executor.run(request))
    assert not (request.node_dir / "transcript.jsonl").exists()  # no SDK call was ever made


@pytest.mark.os_agnostic
def test_run_raises_kernel_error_for_a_cwd_outside_the_isolation_root_before_any_dispatch(tmp_path: Path) -> None:
    """A cwd outside the isolation root is a config bug, checked by ``_cwd_rel`` in
    ``run()`` itself, BEFORE ``ClaudeSDKClient`` is ever constructed AND before
    ``run()``'s own broad except-Exception guard - it must propagate, never come back
    as a FAILED/``executor_error`` record ``run()`` itself produced.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    outside_cwd = tmp_path / "elsewhere"
    outside_cwd.mkdir()
    request = _request(tmp_path, cwd=outside_cwd)
    with pytest.raises(KernelError, match="isolation_root"):
        asyncio.run(executor.run(request))
    assert not (request.node_dir / "transcript.jsonl").exists()  # no SDK call was ever made


@pytest.mark.os_agnostic
def test_build_options_env_blanks_every_non_allowlisted_inherited_variable_under_the_keyfile_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TRUE allowlist, not a secret-name guess: ``SSH_AUTH_SOCK`` and
    ``AWS_ACCESS_KEY_ID`` name neither token/secret/password/authorization/credential,
    so a regex-based blanklist would have let both through - this proves neither does,
    while PATH/HOME and the credential itself survive. Drives the PUBLIC
    ``build_options_env`` directly (no ``reportPrivateUsage`` ignore needed) - it is
    the pure env-building half of ``_options_for``, hoisted out for exactly this.
    """
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKEFAKEFAKEFAKE")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "operator-own-value-must-never-leak")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-FROM-KEYFILE\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    request = _request(tmp_path)
    env = executor.build_options_env(request)
    assert env["SSH_AUTH_SOCK"] == ""
    assert env["AWS_ACCESS_KEY_ID"] == ""
    # The keyfile path carries the NODE's own credential value, not the coordinator's
    # own inherited CLAUDE_CODE_OAUTH_TOKEN - the merge order keeps it, never blanks it.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-FROM-KEYFILE"
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == str(request.node_dir / "home")


@pytest.mark.os_agnostic
def test_build_options_env_blanks_the_inherited_oauth_token_under_the_credential_copy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy path carries NO ``CLAUDE_CODE_OAUTH_TOKEN`` of its own, so the
    coordinator's own inherited one must be blanked, not merged through. Drives the
    PUBLIC ``build_options_env`` directly, same reasoning as the keyfile-path test
    above.
    """
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "operator-own-value-must-never-leak")
    src = tmp_path / "creds.json"
    src.write_text('{"t": 1}')
    executor = ClaudeExecutor(CredentialCopy(src), deny_bash=())
    env = executor.build_options_env(_request(tmp_path))
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


@pytest.mark.os_agnostic
def test_build_options_env_pins_the_remember_plugin_stamp_to_stable(tmp_path: Path) -> None:
    """The operator's ``remember`` plugin registers a ``UserPromptSubmit`` hook whose shell
    reads ``_REMEMBER_STAMP="${REMEMBER_PROMPT_STAMP:-full}"`` - an EXPLICIT positive value
    is required because ``${VAR:-default}`` substitutes on unset OR EMPTY, so this dispatch's
    own ``_blank_everything_else`` writing ``""`` for every inherited name it does not decide
    to carry is indistinguishable, to that hook, from the variable never having been set: both
    fall through to ``full``, whose wall clock and live context percentage change on every
    dispatch and defeat prompt-cache reuse for everything the stamp precedes. Asserts on the
    VALUE, not on absence/falsiness - a test phrased either of those ways would pass before
    the fix, after it, and again if the fix were later removed, proving nothing.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    env = executor.build_options_env(_request(tmp_path))
    assert env.get("REMEMBER_PROMPT_STAMP") == "stable"


@pytest.mark.os_agnostic
def test_build_options_env_raises_kernel_error_for_an_unknown_effort(tmp_path: Path) -> None:
    """The effort validation hoisted into ``build_options_env`` fires on its own,
    independent of :meth:`ClaudeExecutor._options_for` - proves it is not just a
    side effect of the other test's coverage of that private method.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    with pytest.raises(KernelError, match="turbo"):
        executor.build_options_env(_request(tmp_path, effort="turbo"))


@pytest.mark.os_agnostic
def test_result_translation_sums_the_three_input_fields_and_names_auth_failure() -> None:
    o = outcome_from_usage(
        model="sonnet",
        num_turns=3,
        is_error=False,
        text="",
        usage={
            "input_tokens": 50,
            "cache_creation_input_tokens": 3873,
            "cache_read_input_tokens": 119786,
            "output_tokens": 1034,
        },
        first_turn_input=3923,
        cwd_rel="wt/r",
        cost_usd=None,
    )
    assert o.tokens is not None
    assert o.tokens.in_ == 50 + 3873 + 119786
    # `tokens.in_` keeps summing all three input fields - that is the CONTEXT figure.
    # What is CHARGED excludes exactly one of them, the 119,786 cache read, and that single
    # exclusion is the whole difference between the two numbers.
    assert o.charged_tokens == {"sonnet": 50 + 3873 + 1034}
    assert o.status == "done"
    bad = outcome_from_usage(
        model="sonnet",
        num_turns=0,
        is_error=True,
        text="Not logged in - Please run /login",
        usage={},
        first_turn_input=0,
        cwd_rel="wt/r",
        cost_usd=None,
    )
    assert bad.status == "failed"
    assert bad.error is not None
    assert bad.error.type == "auth_failure"
    assert bad.error.transient is False


@pytest.mark.os_agnostic
def test_result_translation_names_a_non_auth_error_as_executor_error_and_transient() -> None:
    o = outcome_from_usage(
        model="sonnet",
        num_turns=1,
        is_error=True,
        text="internal server error",
        usage={},
        first_turn_input=0,
        cwd_rel="wt/r",
        cost_usd=None,
    )
    assert o.status == "failed"
    assert o.error is not None
    assert o.error.type == "executor_error"
    assert o.error.transient is True


@pytest.mark.os_agnostic
def test_result_translation_missing_usage_fields_default_to_zero() -> None:
    o = outcome_from_usage(
        model="sonnet",
        num_turns=1,
        is_error=False,
        text="ok",
        usage={},
        first_turn_input=0,
        cwd_rel="wt/r",
        cost_usd=None,
    )
    assert o.tokens is not None
    assert o.tokens.in_ == 0
    assert o.tokens.out == 0
    assert o.charged_tokens == {"sonnet": 0}


# ---------------------------------------------------------------------------------
# M3: the token cap's two call sites. The per-run call site (Coordinator._run_cap_refusal)
# is covered in test_kernel_context.py, where the caller that owns tokens_by_row and
# the policy ceiling lives. Below is the per-node, per-turn call site: _on_turn's own
# comparison (a pure unit test against a bare interrupt() double, no stream at all),
# then _run's use of it end-to-end against a fake ClaudeSDKClient stream - the SDK
# client construction is this module's own documented external edge (see the module
# docstring), the same reasoning that already keeps every other test in this file off
# a real SDK/network call.
# ---------------------------------------------------------------------------------


class _RecordingInterruptClient:
    """A bare double for ``_Interruptible``: only the one method ``_on_turn`` calls."""

    def __init__(self) -> None:
        self.interrupt_calls = 0

    async def interrupt(self) -> None:
        """Record that this dispatch was asked to stop."""
        self.interrupt_calls += 1


def _turn(usage_input: int) -> AssistantMessage:
    """Build a minimal ``AssistantMessage`` whose usage sums to ``usage_input`` input tokens."""
    return AssistantMessage(content=[], model="sonnet", usage={"input_tokens": usage_input})


def _turn_usage(input_tokens: int, *, output_tokens: int = 0, cache_read_input_tokens: int = 0) -> AssistantMessage:
    """Build an ``AssistantMessage`` whose usage carries BOTH input and output tokens.

    Unlike :func:`_turn` (input-only, for tests that only need :func:`input_total`),
    this is for tests that exercise :meth:`ClaudeExecutor._run`'s running-total sum,
    which is :func:`new_tokens` per turn - the same figure :func:`outcome_from_usage`
    charges. ``output_tokens`` and ``cache_read_input_tokens`` are still settable here
    precisely so a test can prove they are NOT charged.
    """
    usage: dict[str, Any] = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if cache_read_input_tokens:
        usage["cache_read_input_tokens"] = cache_read_input_tokens
    return AssistantMessage(content=[], model="sonnet", usage=usage)


def _result(*, is_error: bool, subtype: str, num_turns: int, usage_input: int = 0) -> ResultMessage:
    """Build a minimal terminal ``ResultMessage`` with a fixed shape for these tests."""
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=num_turns,
        session_id="s1",
        usage={"input_tokens": usage_input, "output_tokens": 3},
        result="" if is_error else "ok",
    )


class FakeStreamClient:
    """A ``ClaudeSDKClient`` stand-in that replays whatever :meth:`configure` last staged.

    ``_run`` constructs its client as ``ClaudeSDKClient(options=options)`` with no
    other argument, so the message sequence a test wants replayed cannot be passed at
    construction time - it is staged first via :meth:`configure` (a queue of one) and
    consumed by the NEXT construction. Kept as one module-level class (never a class
    built per test inside a closure) purely so every attribute below is concretely
    typed under pyright strict - a factory returning a locally-defined nested class
    cannot forward-reference that class in its own return annotation, which pyright
    then reports as ``type[Unknown]`` at every call site.

    Mirrors the probe's own measurement (``workflow/design/probes/m3-interrupt.md``
    in RESEARCH): once ``interrupt()`` is called, no further turn is delivered, but
    exactly one terminal ``ResultMessage`` still arrives - what makes the
    both-SDK-shapes test meaningful is that the CALLER, never this fake, decides the
    outcome from that message.
    """

    _pending: ClassVar[tuple[list[AssistantMessage], ResultMessage] | None] = None
    instances: ClassVar[list[FakeStreamClient]] = []

    def __init__(self, *, options: object) -> None:
        self.options = options
        self.interrupt_calls = 0
        self.turns_yielded = 0
        self.queried_with: str | None = None
        pending = type(self)._pending
        assert pending is not None, "FakeStreamClient.configure() was not called before construction"
        self._turns, self._result = pending
        type(self)._pending = None
        type(self).instances.append(self)

    @classmethod
    def configure(cls, turns: list[AssistantMessage], result: ResultMessage) -> None:
        """Stage the message sequence the NEXT construction will replay, and reset ``instances``."""
        cls._pending = (turns, result)
        cls.instances = []

    async def __aenter__(self) -> FakeStreamClient:
        """Return self, matching ``ClaudeSDKClient``'s own context-manager protocol."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """No-op: nothing this fake holds needs releasing."""
        return None

    async def query(self, prompt: str) -> None:
        """Record the prompt this dispatch queried with."""
        self.queried_with = prompt

    async def receive_response(self) -> AsyncIterator[AssistantMessage | ResultMessage]:
        """Yield the staged turns, stopping early once interrupted, then one terminal ResultMessage."""
        for turn in self._turns:
            yield turn
            self.turns_yielded += 1
            if self.interrupt_calls:
                break
        yield self._result

    async def interrupt(self) -> None:
        """Record that this dispatch was asked to stop."""
        self.interrupt_calls += 1


@pytest.mark.os_agnostic
def test_on_turn_interrupts_once_the_running_total_passes_the_cap(
    tmp_path: Path,
) -> None:
    """Compares the RUNNING TOTAL - :meth:`ClaudeExecutor._run`'s cumulative sum of
    every turn's own spend so far - against ``cap``, not a single turn's figure alone
    (that is the regression this fix round closed: see
    ``test_run_interrupts_when_the_running_total_crosses_the_cap_even_though_no_single_turn_does``
    for the end-to-end proof). A running total of 100 stays under a cap of 200; the
    NEXT turn pushes it to 350, which crosses.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    client = _RecordingInterruptClient()

    first = asyncio.run(executor._on_turn(100, client, 200))  # pyright: ignore[reportPrivateUsage]
    assert first is False
    assert client.interrupt_calls == 0
    second = asyncio.run(executor._on_turn(350, client, 200))  # pyright: ignore[reportPrivateUsage]
    assert second is True
    assert client.interrupt_calls == 1
    # No cap declared for this row at all: never enforced, whatever the running total.
    third = asyncio.run(executor._on_turn(10_000, client, None))  # pyright: ignore[reportPrivateUsage]
    assert third is False
    assert client.interrupt_calls == 1


@pytest.mark.os_agnostic
def test_run_stops_the_stream_at_the_turn_that_crosses_the_cap_and_a_higher_cap_lets_all_three_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    turns = [_turn(100), _turn(250), _turn(400)]
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=200)))
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.type == "budget_exceeded"
    instance = FakeStreamClient.instances[0]
    assert instance.interrupt_calls == 1
    assert instance.turns_yielded == 2  # the third turn was never even seen

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=3))
    outcome2 = asyncio.run(executor.run(_request(tmp_path, token_cap=10_000)))
    assert outcome2.status == "done"


@pytest.mark.os_agnostic
def test_run_interrupts_when_the_running_total_crosses_the_cap_even_though_no_single_turn_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression proof for this fix round: three turns of 80 input tokens each,
    NONE of which alone reaches a cap of 200, whose RUNNING TOTAL (80, then 160, then
    240) crosses it on the third. Against the pre-fix code - which compared each
    turn's own ``input_total`` to ``cap`` and never summed - this cap could never
    fire here: 80 <= 200 on every single turn, so the dispatch would run to
    completion having spent 240, thirty over its own declared cap.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    turns = [_turn(80), _turn(80), _turn(80)]
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=3))
    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=200)))
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.type == "budget_exceeded"
    instance = FakeStreamClient.instances[0]
    assert instance.interrupt_calls == 1
    assert instance.turns_yielded == 3  # crossed only once the third turn's own usage landed

    # Control: the same per-turn shape, but a cap above the eventual total (240) -
    # never interrupted, runs to completion.
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=3))
    outcome2 = asyncio.run(executor.run(_request(tmp_path, token_cap=1_000)))
    assert outcome2.status == "done"
    control_instance = FakeStreamClient.instances[0]
    assert control_instance.interrupt_calls == 0
    assert control_instance.turns_yielded == 3


@pytest.mark.os_agnostic
def test_on_turn_s_running_total_is_pinned_to_the_same_unit_as_charged_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unit-pinning proof: the number the cap compares and the number the record
    charges must be one figure, in one unit.

    Two turns charging 45 and 65, replayed against a terminal ``ResultMessage.usage``
    also totalling 110 (mirroring the real SDK, whose terminal usage is the cumulative
    dispatch total rather than one call's snapshot). At cap=110 the running total lands
    exactly ON the cap and must NOT interrupt (``_on_turn`` uses ``<=``); the dispatch
    completes and ``charged_tokens`` reports the identical 110. Drop the cap to 109 and
    the SAME running total crosses it, landing ``_budget_outcome``, which reports 110 too.

    Both sides carry a cache-read field that must NOT be charged - 10 on the second turn,
    99,999 on the terminal usage. That asymmetry is deliberate: if either side starts
    counting cache reads again the two will disagree here by wildly different amounts,
    rather than drifting together and staying equal while both go wrong.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    turns = [
        _turn_usage(40, output_tokens=5),  # contributes 45
        _turn_usage(60, output_tokens=5, cache_read_input_tokens=10),  # 65; the 10 cache read is NOT charged
    ]  # running total after both turns: 110
    terminal = ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id="s1",
        # The same 110, in terminal-usage shape, with a large cache read alongside to prove
        # the ONE excluded field never reaches the charge from either side.
        usage={"input_tokens": 105, "output_tokens": 5, "cache_read_input_tokens": 99_999},
        result="ok",
    )
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)

    FakeStreamClient.configure(turns, terminal)
    at_cap = asyncio.run(executor.run(_request(tmp_path, token_cap=110)))
    assert at_cap.status == "done"  # running_total (110) <= cap (110): never interrupted
    assert at_cap.charged_tokens == {"sonnet": 110}
    assert FakeStreamClient.instances[0].interrupt_calls == 0

    FakeStreamClient.configure(turns, terminal)
    one_under = asyncio.run(executor.run(_request(tmp_path, token_cap=109)))
    assert one_under.status == "failed"
    assert one_under.error is not None
    assert one_under.error.type == "budget_exceeded"
    assert one_under.charged_tokens == {"sonnet": 110}  # the SAME figure, via the interrupted path
    assert FakeStreamClient.instances[0].interrupt_calls == 1


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("is_error", "subtype"),
    [(False, "success"), (True, "error_during_execution")],
    ids=["turn_boundary_success_shaped", "mid_tool_error_shaped"],
)
def test_a_capped_node_s_record_is_failed_budget_exceeded_regardless_of_the_sdk_s_own_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_error: bool, subtype: str
) -> None:
    """Both shapes the probe measured (``workflow/design/probes/m3-interrupt.md`` in
    RESEARCH): a turn-boundary interrupt reports itself ``is_error=False,
    subtype="success"`` - indistinguishable from a node that finished; a mid-tool
    interrupt reports ``is_error=True, subtype="error_during_execution"`` - the
    opposite. NEITHER may decide this node's outcome: both must land
    ``BUDGET_EXCEEDED``, ``transient=False``, with no artefact ref at all (never the
    half-finished worktree presented as a completed one).
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    turns = [_turn(500)]  # crosses a cap of 100 on the very first turn
    result = _result(is_error=is_error, subtype=subtype, num_turns=1, usage_input=500)
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    FakeStreamClient.configure(turns, result)

    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=100)))

    assert outcome.status == "failed"
    assert outcome.artefact_refs == []  # never the half-finished worktree, whichever SDK shape
    assert outcome.error is not None
    assert outcome.error.type == "budget_exceeded"
    assert outcome.error.transient is False  # never retried into spending the cap again
    assert outcome.key_facts.get("cap_hit") is True
    assert outcome.charged_tokens == {"sonnet": 500 + 3}  # the terminal usage the SDK still reported
    assert FakeStreamClient.instances[0].interrupt_calls == 1


@pytest.mark.os_agnostic
def test_a_node_with_no_declared_cap_is_never_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control for the whole call site: ``ExecutorRequest.token_cap`` defaults to
    ``None`` (nothing set it), and a dispatch under it must run to a normal DONE
    outcome with ``interrupt()`` never called.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    turns = [_turn(999_999)]  # would cross any real cap, but none is declared
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=1))

    outcome = asyncio.run(executor.run(_request(tmp_path)))  # token_cap not overridden: None

    assert outcome.status == "done"
    assert FakeStreamClient.instances[0].interrupt_calls == 0


class _NoTerminalStreamClient:
    """A ``ClaudeSDKClient`` stand-in whose stream ends with NO terminal ``ResultMessage`` at all.

    ``FakeStreamClient`` above always yields its staged ``ResultMessage`` after the
    turns loop, whether or not it was interrupted early - that is exactly what the
    two SDK-shape tests need (the probe measured a terminal message always arrives,
    in one of two shapes). This fake covers the ONE branch neither of those reaches:
    ``ClaudeExecutor._run``'s own ``if cap_hit: return self._budget_outcome(request,
    first_turn_input, usage, cost_usd=cost_usd)`` sitting AFTER the ``async with`` block -
    reached only when the stream closes with no terminal message whatsoever (a connection
    drop right after the interrupted turn, before the SDK's own terminal message would have
    arrived), so both the usage and the cost it is handed there are empty.
    """

    instances: ClassVar[list[_NoTerminalStreamClient]] = []

    def __init__(self, *, options: object) -> None:
        self.options = options
        self.interrupt_calls = 0
        type(self).instances.append(self)

    async def __aenter__(self) -> _NoTerminalStreamClient:
        """Return self, matching ``ClaudeSDKClient``'s own context-manager protocol."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """No-op: nothing this fake holds needs releasing."""
        return None

    async def query(self, prompt: str) -> None:
        """Record the prompt this dispatch queried with (unused by the test, kept for shape parity)."""

    async def receive_response(self) -> AsyncIterator[AssistantMessage | ResultMessage]:
        """Yield one over-cap turn, then end the stream with no ``ResultMessage`` at all."""
        yield _turn(500)  # crosses any small cap on the very first turn

    async def interrupt(self) -> None:
        """Record that this dispatch was asked to stop."""
        self.interrupt_calls += 1


@pytest.mark.os_agnostic
def test_run_reports_budget_exceeded_with_empty_usage_when_the_stream_ends_with_no_terminal_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one branch neither SDK-shape test reaches: ``_run``'s own
    ``if cap_hit: return self._budget_outcome(request, first_turn_input, usage,
    cost_usd=cost_usd)`` after the ``async with`` block - a capped dispatch whose stream
    ends with no terminal ``ResultMessage`` at all. Both
    ``test_a_capped_node_s_record_is_failed_budget_exceeded_regardless_of_the_sdk_s_own_shape``
    cases go through a ``ResultMessage`` arriving (one of the two shapes the probe
    measured); this one never gets one, so ``_budget_outcome`` is called with an EMPTY
    usage mapping rather than a terminal one - it must still report a well-formed
    ``BUDGET_EXCEEDED`` record (zero charged tokens, since no terminal usage was ever
    seen), not fall through to the generic "no ResultMessage" ``EXECUTOR_ERROR`` a few
    lines below it in the source.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", _NoTerminalStreamClient)

    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=100)))

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.type == "budget_exceeded"
    assert outcome.error.transient is False
    assert outcome.artefact_refs == []
    assert outcome.key_facts.get("cap_hit") is True
    assert outcome.charged_tokens == {"sonnet": 0}  # no terminal usage ever arrived
    assert _NoTerminalStreamClient.instances[0].interrupt_calls == 1


# ---------------------------------------------------------------------------------
# M3: the node deadline, the SAME turn seam as the token cap above but a DIFFERENT
# quantity - elapsed WALL-CLOCK SECONDS since dispatch start, never a token count. Every
# test below either drives a clock double directly (proving the comparison reads TIME)
# or is built so a mutation that swapped the deadline check for the token-cap one, or
# vice versa, would fail it - the "unit trap" the task brief named explicitly.
# ---------------------------------------------------------------------------------

_STARTED = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)


class _SequenceClock:
    """A ``Clock`` stand-in returning each of a pre-programmed sequence of readings in turn.

    ``ClaudeExecutor._run`` reads the clock once at dispatch start, then once more per
    ``AssistantMessage`` arrival (:meth:`ClaudeExecutor._deadline_exceeded`) - this fake
    is built with exactly that many readings, in order, so a test pins precisely what
    elapsed time each check saw without depending on real wall-clock timing at all.
    Clamps at the last reading rather than raising if over-read, so a test that is off by
    one call fails on an assertion rather than an unrelated ``IndexError``.
    """

    def __init__(self, readings: list[datetime]) -> None:
        self._readings = list(readings)
        self._index = 0

    def now(self) -> datetime:
        """Return the next staged reading, or the last one if this instance is over-read."""
        reading = self._readings[min(self._index, len(self._readings) - 1)]
        self._index += 1
        return reading


@pytest.mark.os_agnostic
def test_deadline_exceeded_compares_elapsed_seconds_never_a_token_count(tmp_path: Path) -> None:
    """Direct unit test of the comparison itself, mirroring
    ``test_on_turn_interrupts_once_the_running_total_passes_the_cap``'s own shape for the
    token cap. Inclusive at the boundary (``>``, never ``>=``), same reading as
    ``_on_turn``'s own ``<=``: a node may fully spend the deadline it was given.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")

    def executor_at(reading: datetime) -> ClaudeExecutor:
        return ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=_SequenceClock([reading]))

    assert executor_at(_STARTED + timedelta(seconds=10))._deadline_exceeded(_STARTED, 10.0) is False  # pyright: ignore[reportPrivateUsage]
    assert executor_at(_STARTED + timedelta(seconds=11))._deadline_exceeded(_STARTED, 10.0) is True  # pyright: ignore[reportPrivateUsage]
    # No deadline declared at all: never enforced, however much time elapsed.
    assert executor_at(_STARTED + timedelta(days=1))._deadline_exceeded(_STARTED, None) is False  # pyright: ignore[reportPrivateUsage]


@pytest.mark.os_agnostic
def test_run_interrupts_a_node_that_exceeds_its_deadline_and_a_higher_deadline_lets_it_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    turns = [_turn(10), _turn(10)]  # trivial token usage: nowhere near any real cap
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    # Readings: dispatch start, then after turn 1 (5s elapsed), then after turn 2 (15s).
    readings = [_STARTED, _STARTED + timedelta(seconds=5), _STARTED + timedelta(seconds=15)]

    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=_SequenceClock(readings))
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    outcome = asyncio.run(executor.run(_request(tmp_path, deadline_s=10.0)))
    assert outcome.status == "cancelled"
    assert outcome.error is not None
    assert outcome.error.type == "deadline"
    instance = FakeStreamClient.instances[0]
    assert instance.interrupt_calls == 1
    assert instance.turns_yielded == 2  # crossed only after the second turn's elapsed reading

    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=_SequenceClock(readings))
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    outcome2 = asyncio.run(executor.run(_request(tmp_path, deadline_s=20.0)))
    assert outcome2.status == "done"
    assert FakeStreamClient.instances[0].interrupt_calls == 0


@pytest.mark.os_agnostic
def test_run_s_deadline_check_reads_the_clock_and_never_the_token_running_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unit-trap proof the task brief named explicitly: a comparison that
    accidentally read ``running_total`` (tokens) instead of elapsed seconds - or vice
    versa - would get BOTH halves of this test backwards.

    Arm 1: a HUGE token running total (1,000, dwarfing any real per-turn cap) but only 1
    REAL second elapsed, against ``deadline_s=500``. A buggy comparison of tokens against
    ``deadline_s`` would read 1,000 > 500 and wrongly interrupt; the real comparison
    (1 second elapsed <= 500) must NOT interrupt.

    Arm 2: the mirror image - a TINY running total (1 token) but 1,000 REAL seconds
    elapsed, against the SAME ``deadline_s=500``. A buggy comparison of tokens against
    ``deadline_s`` would read 1 <= 500 and wrongly let it through; the real comparison
    (1,000 seconds elapsed > 500) must interrupt.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)

    huge_tokens_tiny_time = [_turn(1000)]
    clock_a = _SequenceClock([_STARTED, _STARTED + timedelta(seconds=1)])
    executor_a = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=clock_a)
    FakeStreamClient.configure(huge_tokens_tiny_time, _result(is_error=False, subtype="success", num_turns=1))
    outcome_a = asyncio.run(executor_a.run(_request(tmp_path, deadline_s=500.0)))
    assert outcome_a.status == "done"  # 1 real second elapsed: nowhere near the 500s deadline
    assert FakeStreamClient.instances[0].interrupt_calls == 0

    tiny_tokens_huge_time = [_turn(1)]
    clock_b = _SequenceClock([_STARTED, _STARTED + timedelta(seconds=1000)])
    executor_b = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=clock_b)
    FakeStreamClient.configure(tiny_tokens_huge_time, _result(is_error=False, subtype="success", num_turns=1))
    outcome_b = asyncio.run(executor_b.run(_request(tmp_path, deadline_s=500.0)))
    assert outcome_b.status == "cancelled"  # 1000 real seconds elapsed: past the 500s deadline
    assert outcome_b.error is not None
    assert outcome_b.error.type == "deadline"
    assert FakeStreamClient.instances[0].interrupt_calls == 1


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("is_error", "subtype"),
    [(False, "success"), (True, "error_during_execution")],
    ids=["turn_boundary_success_shaped", "mid_tool_error_shaped"],
)
def test_a_deadline_stopped_node_s_record_is_cancelled_deadline_regardless_of_the_sdk_s_own_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_error: bool, subtype: str
) -> None:
    """Both SDK shapes the probe measured (``workflow/design/probes/m3-interrupt.md`` in
    RESEARCH), same as the budget cap's own equivalent test: NEITHER may decide this
    node's outcome. Both must land ``CANCELLED``/``deadline``, ``transient=False``, no
    artefact ref at all.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    turns = [_turn(1)]  # trivial usage: the DEADLINE is what fires here, not the cap
    result = _result(is_error=is_error, subtype=subtype, num_turns=1, usage_input=1)
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    FakeStreamClient.configure(turns, result)
    clock = _SequenceClock([_STARTED, _STARTED + timedelta(seconds=11)])
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=clock)

    outcome = asyncio.run(executor.run(_request(tmp_path, deadline_s=10.0)))

    assert outcome.status == "cancelled"
    assert outcome.artefact_refs == []
    assert outcome.error is not None
    assert outcome.error.type == "deadline"
    assert outcome.error.transient is False  # never retried straight back into running out of time again
    assert outcome.key_facts.get("deadline_hit") is True
    assert FakeStreamClient.instances[0].interrupt_calls == 1


@pytest.mark.os_agnostic
def test_a_node_with_no_declared_deadline_is_never_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control for the whole call site: ``ExecutorRequest.deadline_s`` defaults to
    ``None``, and a dispatch under it runs to a normal DONE outcome, whatever the clock
    reports elapsing.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    turns = [_turn(1)]
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=1))
    clock = _SequenceClock([_STARTED, _STARTED + timedelta(days=365)])  # absurdly over any real deadline
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=clock)

    outcome = asyncio.run(executor.run(_request(tmp_path)))  # deadline_s not overridden: None

    assert outcome.status == "done"
    assert FakeStreamClient.instances[0].interrupt_calls == 0


@pytest.mark.os_agnostic
def test_run_reports_deadline_with_empty_usage_when_the_stream_ends_with_no_terminal_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline's own equivalent of
    ``test_run_reports_budget_exceeded_with_empty_usage_when_the_stream_ends_with_no_terminal_message``:
    ``_run``'s own ``if deadline_hit: return self._deadline_outcome(request,
    first_turn_input, usage, cost_usd=cost_usd)`` after the ``async with`` block, reached
    when the stream ends with no terminal ``ResultMessage`` at all.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", _NoTerminalStreamClient)
    clock = _SequenceClock([_STARTED, _STARTED + timedelta(seconds=11)])
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=clock)

    outcome = asyncio.run(executor.run(_request(tmp_path, deadline_s=10.0)))

    assert outcome.status == "cancelled"
    assert outcome.error is not None
    assert outcome.error.type == "deadline"
    assert outcome.error.transient is False
    assert outcome.artefact_refs == []
    assert outcome.key_facts.get("deadline_hit") is True
    assert outcome.charged_tokens == {"sonnet": 0}  # no terminal usage ever arrived
    assert _NoTerminalStreamClient.instances[0].interrupt_calls == 1


@pytest.mark.os_agnostic
def test_write_hook_denies_an_in_root_path_the_node_never_declared(tmp_path: Path) -> None:
    """Prevention is per-node, so it is judged on THIS node's write set, not the run's."""
    root = tmp_path / "run"
    (root / "wt" / "a").mkdir(parents=True)
    (root / "wt" / "b").mkdir(parents=True)
    hook = deny_outside_write_set(root, allowed=("wt/a/**",))

    assert fire(hook, "Write", {"file_path": str(root / "wt" / "a" / "f.py")}) is None
    assert fire(hook, "Write", {"file_path": str(root / "wt" / "b" / "f.py")}) == "deny"


@pytest.mark.os_agnostic
def test_write_hook_allows_the_node_its_own_dir_when_the_caller_grants_it(tmp_path: Path) -> None:
    """A node writes its own artefacts without declaring them; the caller adds that grant."""
    root = tmp_path / "run"
    node_dir = root / "nodes" / "w1" / "0000abcd"
    node_dir.mkdir(parents=True)
    hook = deny_outside_write_set(root, allowed=("nodes/w1/0000abcd/**",))

    assert fire(hook, "Write", {"file_path": str(node_dir / "artefacts" / "out.json")}) is None
    assert fire(hook, "Write", {"file_path": str(root / "nodes" / "w2" / "0000dcba" / "out.json")}) == "deny"


@pytest.mark.os_agnostic
def test_write_hook_with_nothing_allowed_denies_every_write(tmp_path: Path) -> None:
    """An empty grant means write nothing, not write anywhere in the root."""
    root = tmp_path / "run"
    (root / "wt").mkdir(parents=True)
    hook = deny_outside_write_set(root, allowed=())

    assert fire(hook, "Write", {"file_path": str(root / "wt" / "f.py")}) == "deny"


@pytest.mark.os_agnostic
def test_the_executor_grants_a_node_its_write_set_and_its_own_dir(tmp_path: Path) -> None:
    """The rule for what a node may write, in one place, derived from its own request."""
    request = _request(tmp_path, write_set=("wt/r/**",))

    allowed = allowed_writes(request)

    assert "wt/r/**" in allowed
    assert f"{request.node_dir.relative_to(request.isolation_root).as_posix()}/**" in allowed


@pytest.mark.os_agnostic
def test_a_node_may_still_write_in_the_worktree_it_was_given(tmp_path: Path) -> None:
    """The composition, on the shape graph A dispatches: cwd is the declared worktree."""
    request = _request(tmp_path, write_set=("wt/r/**",))
    hook = deny_outside_write_set(request.isolation_root, allowed=allowed_writes(request))

    assert fire(hook, "Write", {"file_path": str(request.cwd / "src" / "mod.py")}) is None
    assert fire(hook, "Edit", {"file_path": str(request.node_dir / "notes.md")}) is None
    assert fire(hook, "Write", {"file_path": str(request.isolation_root / "wt" / "other" / "mod.py")}) == "deny"


def _turn_of_message(message_id: str, usage_input: int) -> AssistantMessage:
    """One streamed event of the API request ``message_id``, carrying that request's usage.

    The CLI emits one ``AssistantMessage`` event PER CONTENT BLOCK of a single
    assistant message, and every one of them repeats the SAME ``message_id`` and the
    SAME ``usage``. Measured on the stored dispatches under the run store: 19 events
    over 12 distinct message ids, and 10/6, 24/16, 41/23, 26/17 - so a running sum that
    adds every event counts each API request 1.5 to 1.8 times.
    """
    return AssistantMessage(content=[], model="sonnet", usage={"input_tokens": usage_input}, message_id=message_id)


@pytest.mark.os_agnostic
def test_the_running_total_counts_one_api_request_once_however_many_blocks_it_arrives_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two API requests of 100, the first arriving as two blocks: the total is 200, not 300.

    Counting per EVENT instead of per REQUEST inflates the figure the cap compares, so a
    node is interrupted while its real usage is well inside its cap - measured live, a
    dispatch holding about 250000 read as past a 400000 cap and its correct work was
    discarded unexamined.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    turns = [
        _turn_of_message("msg_1", 100),  # request 1, block 1
        _turn_of_message("msg_1", 100),  # request 1, block 2 - the SAME usage, not a second charge
        _turn_of_message("msg_2", 100),  # request 2
    ]

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=250)))

    assert outcome.status == "done"  # two requests of 100 is 200, under the cap of 250
    assert FakeStreamClient.instances[0].interrupt_calls == 0

    # Control: the same stream against a cap BELOW the deduplicated total still interrupts,
    # so the assertion above is about double counting and not about the cap being disarmed.
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    under = asyncio.run(executor.run(_request(tmp_path, token_cap=150)))
    assert under.status == "failed"
    assert under.error is not None
    assert under.error.type == "budget_exceeded"
    assert FakeStreamClient.instances[0].interrupt_calls == 1


@pytest.mark.os_agnostic
def test_a_node_past_its_context_ceiling_ends_needs_continuation_and_keeps_its_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The context ceiling (design 3.8) is a THIRD quantity at the same turn seam.

    It compares ONE turn's own ``input_total`` - what the model just saw - against
    ``handover_at_tokens``, never the running sum the token cap uses: a context ceiling
    asks "is the window full right now", which a sum that only grows cannot answer.

    Crossing it is not a failure. The node ends ``needs_continuation`` and KEEPS its
    artefact ref, because its worktree holds real work a successor continues from -
    unlike the cap and deadline paths, which empty ``artefact_refs`` so a half-finished
    tree is never presented as a completed one.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    turns = [_turn_of_message("m1", 100), _turn_of_message("m2", 200)]  # turn 2's own context is 200

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    outcome = asyncio.run(
        executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=150)),
    )

    assert outcome.status == "needs_continuation"
    assert outcome.error is None  # a handover is not a failure
    assert outcome.artefact_refs == ["wt/r"]  # the work survives, unlike the cap path
    assert outcome.key_facts.get("context_at_handover") == 200
    # Crossing ARMS the stop notice, it does not interrupt (decision 14): the node is being
    # asked to write its handover, so stopping it here would guarantee no record exists.
    # It is interrupted only if it works on past HANDOVER_GRACE_TURNS, which this two-turn
    # stream never reaches - see
    # test_a_node_that_ignores_the_stop_notice_is_interrupted_once_the_grace_runs_out.
    assert FakeStreamClient.instances[0].interrupt_calls == 0

    # Control: the same stream under a ceiling no single turn reaches runs to completion,
    # even though the SUM of the two turns (300) is well past it. The ceiling is per turn.
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    under = asyncio.run(
        executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=250)),
    )
    assert under.status == "done"
    assert FakeStreamClient.instances[0].interrupt_calls == 0


@pytest.mark.os_agnostic
def test_stop_notice_hook_says_nothing_until_it_is_armed() -> None:
    """Before the ceiling is crossed the hook must be silent, not merely harmless.

    A node under its ceiling never sees the nudge - that is design 3.8's own control row.
    """
    hook = inject_stop_notice(lambda: False, handover_path="/r/nodes/n1/handover.json")

    assert asyncio.run(_await_hook(hook, "Write", {"file_path": "/r/wt/a/f.py"})) == {}


@pytest.mark.os_agnostic
def test_stop_notice_hook_injects_the_notice_once_armed() -> None:
    """Armed, it puts the authorised stop notice in front of the model."""
    hook = inject_stop_notice(lambda: True, handover_path="/r/nodes/n1/handover.json")

    result = asyncio.run(_await_hook(hook, "Write", {"file_path": "/r/wt/a/f.py"}))

    specific = result["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert "/r/nodes/n1/handover.json" in specific["additionalContext"]
    assert "run coordinator" in specific["additionalContext"].lower()


@pytest.mark.os_agnostic
def test_stop_notice_hook_never_carries_a_permission_decision() -> None:
    """The measured shape: inject only, so the call still runs (decision 14).

    The probe measured the hooked call running in 40 of 40 dispatches with an inject-only
    return. A ``permissionDecision`` here would turn the nudge into a block, and the node
    would be stopped before it could write the very handover it is being asked for.
    """
    hook = inject_stop_notice(lambda: True, handover_path="/r/nodes/n1/handover.json")

    result = asyncio.run(_await_hook(hook, "Write", {"file_path": "/r/wt/a/f.py"}))

    assert "permissionDecision" not in result["hookSpecificOutput"]
    assert fire(hook, "Write", {"file_path": "/r/wt/a/f.py"}) is None


@pytest.mark.os_agnostic
def test_stop_notice_hook_reads_the_flag_at_call_time_not_at_build_time() -> None:
    """The executor arms it mid-dispatch, so the hook must re-read the predicate.

    A closure that captured the value at build time would be armed never or always - the
    same class of bug as the body closure that captured ``work()``'s original spec.
    """
    armed = False
    hook = inject_stop_notice(lambda: armed, handover_path="/r/h.json")

    assert asyncio.run(_await_hook(hook, "Write", {"file_path": "/r/wt/a/f.py"})) == {}
    armed = True
    assert "hookSpecificOutput" in asyncio.run(_await_hook(hook, "Write", {"file_path": "/r/wt/a/f.py"}))


def _fire_every_pretooluse_hook(options: object, tool_name: str, tool_input: dict[str, Any]) -> list[dict[str, Any]]:
    """Fire every ``PreToolUse`` hook the built options carry, and collect what they returned.

    Deliberately not "reach into matcher number three": which matcher holds which hook is
    an ordering detail, and a test that hard-codes it passes while the wiring rots. Firing
    all of them and asking what came back tests the property that matters - is the stop
    notice REACHABLE from the options this dispatch actually built.
    """
    hooks_by_event = cast("dict[str, list[Any]]", getattr(options, "hooks", {}) or {})
    return [
        asyncio.run(_await_hook(hook, tool_name, tool_input))
        for matcher in hooks_by_event.get("PreToolUse", [])
        for hook in cast("list[HookCallback]", matcher.hooks)
    ]


@pytest.mark.os_agnostic
def test_crossing_the_ceiling_asks_the_node_to_hand_over_before_interrupting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crossing arms the notice; it does not stop the node on the spot (decision 14).

    An immediate ``interrupt()`` is what the code did before, and it cannot work: the node
    is being asked to WRITE its handover, so stopping it at the moment of asking guarantees
    there is no record for the successor to read.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    turns = [_turn_of_message("m1", 100), _turn_of_message("m2", 200)]  # turn 2 crosses a 150 ceiling

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=150)))

    assert outcome.status == "needs_continuation"
    assert FakeStreamClient.instances[0].interrupt_calls == 0  # the stream ended inside the grace


@pytest.mark.os_agnostic
def test_a_node_that_ignores_the_stop_notice_is_interrupted_once_the_grace_runs_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The grace is bounded: a node that keeps working is stopped anyway.

    Compliance is not guaranteed - measured 4 of 4 under the right framing, but the same
    probe measured 0 of 4 under the wrong one - so the grace must expire rather than let a
    node run on indefinitely after being asked to stop.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    # turn 2 crosses; every later turn stays over, and there are more of them than the grace
    turns = [_turn_of_message(f"m{n}", 100 if n == 1 else 200) for n in range(1, HANDOVER_GRACE_TURNS + 4)]

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=len(turns)))
    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=150)))

    assert outcome.status == "needs_continuation"
    assert FakeStreamClient.instances[0].interrupt_calls == 1


@pytest.mark.os_agnostic
def test_the_handover_record_says_whether_the_grace_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A node that stopped on its own and one that was cut off must not record the same.

    Both end ``needs_continuation`` carrying the same ceiling figures, so without this the
    two are indistinguishable on the record - and telling them apart is the whole question
    a live run is supposed to answer. Measured in RESEARCH
    ``workflow/design/probes/live-handover.md``: 6 armed dispatches, 2 of them at the grace
    threshold, and nothing on the record could classify any of them.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)

    # Stopped on its own: turn 2 crosses a 150 ceiling and the stream ends inside the grace.
    inside = [_turn_of_message("m1", 100), _turn_of_message("m2", 200)]
    FakeStreamClient.configure(inside, _result(is_error=False, subtype="success", num_turns=2))
    voluntary = asyncio.run(executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=150)))

    # Cut off: every turn after the crossing stays over, and there are more than the grace.
    over = [_turn_of_message(f"m{n}", 100 if n == 1 else 200) for n in range(1, HANDOVER_GRACE_TURNS + 4)]
    FakeStreamClient.configure(over, _result(is_error=False, subtype="success", num_turns=len(over)))
    cut_off = asyncio.run(executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=150)))

    assert voluntary.status == "needs_continuation"
    assert cut_off.status == "needs_continuation"
    # Indexed, not .get(): a missing key must fail this test loudly rather than compare as
    # None, which is how the gap being closed here went unnoticed in the first place.
    assert voluntary.key_facts["grace_expired"] is False
    assert cut_off.key_facts["grace_expired"] is True
    assert voluntary.key_facts["grace_used"] < HANDOVER_GRACE_TURNS
    assert cut_off.key_facts["grace_used"] == HANDOVER_GRACE_TURNS
    # Declared TYPED or the coordinator may never branch on it: design 3.3 restricts a branch
    # to keys named in typed_fields and treats every other key_facts entry as free text.
    assert "grace_expired" in cut_off.typed_fields
    assert "grace_used" not in cut_off.typed_fields


@pytest.mark.os_agnostic
def test_the_handover_grace_counts_api_requests_not_stream_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One request's extra content blocks must not spend the grace they arrived with.

    This CLI emits one ``AssistantMessage`` event PER CONTENT BLOCK, repeating that
    request's own ``message_id`` and usage, so a grace folded per EVENT is spent by the
    single turn that armed it. Measured in the grace probe (RESEARCH
    ``workflow/design/probes/handover-grace-expiry.md``, 58 dispatches): a complying node
    needs two requests after the notice and produces three events doing it, so a
    three-EVENT grace expires exactly on that boundary and lost the record 1 time in 8 -
    once with the handover JSON already streaming.

    The same defect was fixed for the token sums in ``dbb5c9e`` by keying on
    ``message_id``; this is the remaining caller that folded per event.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    turns = [
        _turn_of_message("m1", 100),  # under the ceiling
        _turn_of_message("m2", 200),  # crosses it: the notice is armed here
        _turn_of_message("m2", 200),  # the SAME request's second block
        _turn_of_message("m2", 200),  # and its third
        _turn_of_message("m3", 200),  # the node's next turn, where it writes its handover
    ]

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=3))
    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=150)))

    assert outcome.status == "needs_continuation"
    assert FakeStreamClient.instances[0].interrupt_calls == 0


@pytest.mark.os_agnostic
def test_the_stop_notice_hook_is_wired_into_the_dispatch_and_armed_by_the_crossing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice is reachable from the options this dispatch built, and silent until crossed.

    Without this the executor would carry a stop-notice hook nothing ever installs - a
    producer with no consumer, which reads as a working mechanism and is not one.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    in_root_write = {"file_path": str(tmp_path / "run" / "wt" / "r" / "f.py")}

    # A dispatch that never reaches the ceiling: every hook stays silent.
    FakeStreamClient.configure([_turn_of_message("m1", 100)], _result(is_error=False, subtype="success", num_turns=1))
    asyncio.run(executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=10_000)))
    quiet = _fire_every_pretooluse_hook(FakeStreamClient.instances[0].options, "Write", in_root_write)
    assert all("hookSpecificOutput" not in r or "additionalContext" not in r["hookSpecificOutput"] for r in quiet)

    # A dispatch that crosses it: one of the wired hooks now carries the notice.
    FakeStreamClient.configure(
        [_turn_of_message("m1", 100), _turn_of_message("m2", 200)],
        _result(is_error=False, subtype="success", num_turns=2),
    )
    request = _request(tmp_path, token_cap=100_000, handover_at_tokens=150)
    asyncio.run(executor.run(request))
    fired = _fire_every_pretooluse_hook(FakeStreamClient.instances[0].options, "Write", in_root_write)

    notices = [
        r["hookSpecificOutput"]["additionalContext"]
        for r in fired
        if "additionalContext" in (r.get("hookSpecificOutput") or {})
    ]
    assert len(notices) == 1
    assert str(request.node_dir / HANDOVER_FILENAME) in notices[0]
    assert "run coordinator" in notices[0].lower()


def _notice_hook(options: object) -> HookCallback:
    """The stop-notice hook the executor installed, found by its MATCHER not by index.

    Indexing the matcher list would couple this to the order the three PreToolUse hooks
    happen to be registered in; the notice is the only one matched against ``Read``,
    which is what actually identifies it.
    """
    opts = cast("ClaudeAgentOptions", options)
    # Cast to the concrete mapping the executor builds: the SDK types `hooks` as an
    # optional dict whose values pyright reads as partially unknown, so without this every
    # use below is an unknown-type error. Narrowed here once, not silenced at each site.
    hooks = cast("dict[str, list[HookMatcher]]", opts.hooks)
    matchers: list[HookMatcher] = hooks["PreToolUse"]
    found = [m for m in matchers if "Read" in (m.matcher or "")]
    assert len(found) == 1, f"expected exactly one stop-notice matcher, got {len(found)}"
    return cast("HookCallback", found[0].hooks[0])


def _stopped_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ClaudeExecutor:
    """An executor wired to the fake client, for the subtree-stop arms below."""
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    return executor


@pytest.mark.os_agnostic
def test_a_subtree_stop_arms_the_handover_with_no_context_ceiling_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 34 step 2. The node declares NO ceiling, so no context pressure can exist.

    ``handover_at_tokens=None`` makes ``_past_context_ceiling`` answer False whatever the
    usage, so the only thing that can arm this dispatch is the subtree predicate. Without
    the second reason ORed in, the run ends as an ordinary success and no handover record
    is produced at all.
    """
    executor = _stopped_executor(tmp_path, monkeypatch)
    turns = [_turn_of_message("m1", 100), _turn_of_message("m2", 100)]
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))

    outcome = asyncio.run(executor.run(_request(tmp_path, handover_at_tokens=None, is_stopping=lambda: True)))

    assert outcome.status == "needs_continuation"
    assert outcome.key_facts["stopped_by_subtree"] is True
    assert FakeStreamClient.instances[0].interrupt_calls == 0  # the stream ended inside the grace


@pytest.mark.os_agnostic
def test_the_stop_notice_reaches_the_model_once_a_subtree_stop_armed_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice is what the whole mechanism is FOR, so assert on the hook's own output.

    The outcome alone would prove the executor armed something; this proves the thing it
    armed is the object the installed hook reads, by calling that hook and reading the
    text it puts in front of the model.
    """
    executor = _stopped_executor(tmp_path, monkeypatch)
    turns = [_turn_of_message("m1", 100), _turn_of_message("m2", 100)]
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))
    asyncio.run(executor.run(_request(tmp_path, handover_at_tokens=None, is_stopping=lambda: True)))

    hook = _notice_hook(FakeStreamClient.instances[0].options)
    out = asyncio.run(_await_hook(hook, "Read", {}))

    assert HANDOVER_FILENAME in out["hookSpecificOutput"]["additionalContext"]


@pytest.mark.os_agnostic
def test_the_context_ceiling_still_arms_with_no_subtree_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control that says the OR did not REPLACE decision 14's trigger with the new one.

    No predicate is given at all (``is_stopping`` defaults to None, as every call site
    predating this field leaves it), and the record must still say the ceiling was what
    stopped it - a subtree-stop flag that read True here would mean the two reasons had
    been conflated rather than ORed.
    """
    executor = _stopped_executor(tmp_path, monkeypatch)
    turns = [_turn_of_message("m1", 100), _turn_of_message("m2", 200)]
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))

    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=100_000, handover_at_tokens=150)))

    assert outcome.status == "needs_continuation"
    assert outcome.key_facts["stopped_by_subtree"] is False
    assert outcome.key_facts["context_at_handover"] == 200


@pytest.mark.os_agnostic
def test_a_subtree_stopped_node_gets_the_same_grace_before_the_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 34 step 4. Asserted as a COUNT of requests, not as "it was interrupted".

    A zero-grace immediate interrupt would still satisfy "the node was stopped", and would
    then show up only as handovers going missing under load - which is what the grace probe
    measured (58 dispatches; a one-request grace lost the record 8 times out of 8). The
    subtree path must spend the SAME measured grace as the ceiling path, not its own.
    """
    executor = _stopped_executor(tmp_path, monkeypatch)
    turns = [_turn_of_message(f"m{n}", 100) for n in range(1, HANDOVER_GRACE_TURNS + 4)]
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=len(turns)))

    outcome = asyncio.run(executor.run(_request(tmp_path, handover_at_tokens=None, is_stopping=lambda: True)))

    client = FakeStreamClient.instances[0]
    requests_after_the_notice = client.turns_yielded - 1  # turn 1 armed it and is not part of the grace
    assert requests_after_the_notice == HANDOVER_GRACE_TURNS
    assert client.interrupt_calls == 1
    assert outcome.key_facts["grace_expired"] is True
    assert outcome.key_facts["grace_used"] == HANDOVER_GRACE_TURNS


@pytest.mark.os_agnostic
def test_the_subtree_predicate_is_read_at_every_turn_not_captured_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stop requested MID-dispatch must arm the node that is already running.

    That is the whole reason this is a predicate and not a bool: the subtree decides to
    stop while its nodes are in flight, so a dispatch that read the value once before its
    first turn would be armed either never or always. Arming on turn 3 puts the interrupt
    exactly ``HANDOVER_GRACE_TURNS`` requests later, which is what pins WHEN it armed.
    """
    executor = _stopped_executor(tmp_path, monkeypatch)
    seen = {"turns": 0}

    def stopping_from_the_third_turn() -> bool:
        seen["turns"] += 1
        return seen["turns"] >= 3

    turns = [_turn_of_message(f"m{n}", 100) for n in range(1, HANDOVER_GRACE_TURNS + 5)]
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=len(turns)))

    asyncio.run(executor.run(_request(tmp_path, handover_at_tokens=None, is_stopping=stopping_from_the_third_turn)))

    client = FakeStreamClient.instances[0]
    assert client.turns_yielded == 3 + HANDOVER_GRACE_TURNS
    assert client.interrupt_calls == 1


@pytest.mark.os_agnostic
def test_a_subtree_stop_wins_when_both_reasons_fire_on_the_same_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedence is decided here rather than left to whichever operand came first.

    A node whose subtree is stopping is having its plan REPLACED, while one that merely
    crossed its ceiling is being continued - so when one turn triggers both, the record has
    to say the subtree stopped it, or a re-plan reads it as an ordinary continuation.
    """
    executor = _stopped_executor(tmp_path, monkeypatch)
    turns = [_turn_of_message("m1", 200)]  # 200 is over the 150 ceiling AND the subtree is stopping
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=1))

    outcome = asyncio.run(executor.run(_request(tmp_path, handover_at_tokens=150, is_stopping=lambda: True)))

    assert outcome.key_facts["stopped_by_subtree"] is True


@pytest.mark.os_agnostic
def test_stopped_by_subtree_is_typed_so_a_re_plan_may_branch_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design 3.3 lets the coordinator branch ONLY on a key named in ``typed_fields``.

    Task 35 re-plans a stopped subtree, and telling a subtree-stopped node from one that
    crossed its own ceiling is exactly the branch it has to make - so an untyped key here
    would be unreadable to the thing it exists for, the same gap ``grace_expired`` closed.
    """
    executor = _stopped_executor(tmp_path, monkeypatch)
    turns = [_turn_of_message("m1", 100), _turn_of_message("m2", 100)]
    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=2))

    outcome = asyncio.run(executor.run(_request(tmp_path, handover_at_tokens=None, is_stopping=lambda: True)))

    assert "stopped_by_subtree" in outcome.typed_fields


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="symlink_to needs elevated privileges on Windows")
def test_read_hook_denies_a_target_outside_every_root_after_realpath(tmp_path: Path) -> None:
    """The bound the first real plan-goal run went without.

    Both routes out are closed the same way a literal outside path is, because the
    comparison happens after realpath: a symlink planted inside a root, and a `..` segment.
    """
    node_dir = tmp_path / "run" / "nodes" / "p_root"
    cwd = tmp_path / "run" / "wt" / "root"
    node_dir.mkdir(parents=True)
    cwd.mkdir(parents=True)
    (cwd / "out").symlink_to(tmp_path)  # the symlink route out
    hook = deny_reads_outside((node_dir, cwd))

    assert fire(hook, "Read", {"file_path": str(cwd / "a.py")}) is None
    assert fire(hook, "Read", {"file_path": str(node_dir / "brief.md")}) is None
    assert fire(hook, "Read", {"file_path": str(tmp_path / "elsewhere.txt")}) == "deny"
    assert fire(hook, "Read", {"file_path": "/etc/passwd"}) == "deny"
    assert fire(hook, "Read", {"file_path": str(cwd / "out" / "x")}) == "deny"
    assert fire(hook, "Grep", {"path": str(cwd / ".." / ".." / "..")}) == "deny"


@pytest.mark.os_agnostic
def test_read_hook_decides_the_absent_path_per_tool_rather_than_uniformly(tmp_path: Path) -> None:
    """The absent case is two different answers, and guessing one for both is wrong.

    `Grep` and `Glob` take an OPTIONAL path that means the dispatch cwd, which is a root by
    construction, so absence there is a legitimate in-bounds call. `Read` has no such
    default, so an input carrying no path is a shape the hook cannot classify and it fails
    closed - the same reading `deny_outside_write_set` gives a write with no path.
    """
    cwd = tmp_path / "wt"
    cwd.mkdir()
    hook = deny_reads_outside((cwd,))

    assert fire(hook, "Grep", {"pattern": "x"}) is None
    assert fire(hook, "Glob", {"pattern": "**/*.py"}) is None
    assert fire(hook, "Read", {}) == "deny"


@pytest.mark.os_agnostic
def test_read_hook_with_no_roots_denies_every_read(tmp_path: Path) -> None:
    """Empty means confined to nothing, never unconfined - the failure direction that matters."""
    hook = deny_reads_outside(())

    assert fire(hook, "Read", {"file_path": str(tmp_path / "anything")}) == "deny"


@pytest.mark.os_agnostic
def test_confined_bash_is_refused_whatever_the_command_says() -> None:
    """A denylist cannot bound a read set, so a confined node gets no shell at all."""
    hook = deny_every_bash_command("everything you need is in your prompt")

    assert fire(hook, "Bash", {"command": "ls"}) == "deny"
    assert fire(hook, "Bash", {"command": "grep -r x /"}) == "deny"
    assert fire(hook, "Bash", {"command": ""}) == "deny"


@pytest.mark.os_agnostic
def test_denied_tools_get_one_matcher_that_refuses_them_outright(tmp_path: Path) -> None:
    """``deny_tools`` closes the network and sub-agent tools the way a confined node's Bash is closed.

    Asserts on what the matcher DOES: the hook denies whatever the tool input says, and the
    reason names the tool and the config key, so a refused node learns why rather than
    trying another route.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), deny_tools=("WebFetch", "Task"))

    options = executor._options_for(_request(tmp_path), is_stopping=lambda: False)  # pyright: ignore[reportPrivateUsage]

    hook = _only_hook(_pretooluse(options), "WebFetch|Task")
    assert fire(hook, "WebFetch", {"url": "https://example.invalid"}) == "deny"
    assert fire(hook, "Task", {"prompt": "spawn a helper"}) == "deny"
    reason = asyncio.run(_await_hook(hook, "WebFetch", {"url": "https://example.invalid"}))
    assert "WebFetch" in str(reason) and "deny_tools" in str(reason)


@pytest.mark.os_agnostic
def test_a_request_s_own_tool_denylist_wins_and_an_empty_one_registers_no_matcher(tmp_path: Path) -> None:
    """The request carries the run's list; the executor's own is the fallback, and empty on both means no matcher."""
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), deny_tools=("WebSearch",))

    from_request = executor._options_for(  # pyright: ignore[reportPrivateUsage]
        _request(tmp_path, deny_tools=("Task",)), is_stopping=lambda: False
    )
    assert fire(_only_hook(_pretooluse(from_request), "Task"), "Task", {}) == "deny"
    assert not [m for m in _pretooluse(from_request) if m.matcher == "WebSearch"]

    bare = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    none = bare._options_for(_request(tmp_path), is_stopping=lambda: False)  # pyright: ignore[reportPrivateUsage]
    assert not [m for m in _pretooluse(none) if "WebSearch" in (m.matcher or "") or "Task" in (m.matcher or "")]


@pytest.mark.os_agnostic
def test_read_roots_switches_the_bash_matcher_from_a_denylist_to_a_refusal(tmp_path: Path) -> None:
    """The pairing is the point: confinement that left Bash filtered would confine nothing.

    Asserts on what the matchers DO, not on how many there are - a count passes against a
    matcher wired to the wrong hook.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=("git push",))
    unconfined = executor._read_confinement(_request(tmp_path))  # pyright: ignore[reportPrivateUsage]
    confined = executor._read_confinement(  # pyright: ignore[reportPrivateUsage]
        _request(tmp_path, read_roots=(tmp_path / "run" / "nodes" / "n1",))
    )

    bash_unconfined = _only_hook(unconfined, "Bash")
    assert fire(bash_unconfined, "Bash", {"command": "git push"}) == "deny"
    assert fire(bash_unconfined, "Bash", {"command": "ls"}) is None
    assert not [m for m in unconfined if m.matcher == "Read|Grep|Glob"]

    bash_confined = _only_hook(confined, "Bash")
    assert fire(bash_confined, "Bash", {"command": "ls"}) == "deny"
    reads = _only_hook(confined, "Read|Grep|Glob")
    assert fire(reads, "Read", {"file_path": "/etc/passwd"}) == "deny"


@pytest.mark.os_agnostic
def test_the_turn_ceiling_is_a_continuation_not_a_fault() -> None:
    """`error_max_turns` is a bound being reached, and the CLI reports it AS an error.

    Measured 2026-09-02 on the first `spec`-scale run: six work nodes ended
    `subtype="error_max_turns"` at one turn past the ceiling and every one was recorded
    FAILED with a transient EXECUTOR_ERROR. Transient means the retry path re-dispatches
    into the identical wall, and the record named no cause - so the whole run died namelessly
    after spending its budget. It is the same class of event as crossing `handover_at_tokens`,
    which has always ended NEEDS_CONTINUATION and kept its tree.
    """
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 900, "output_tokens": 7}
    hit = outcome_from_usage(
        model="sonnet",
        num_turns=26,
        is_error=True,
        text="",
        usage=usage,
        first_turn_input=21,
        cwd_rel="wt/root",
        subtype="error_max_turns",
        cost_usd=None,
    )
    assert hit.status is NodeStatus.NEEDS_CONTINUATION
    assert hit.error is None, "a ceiling reached is not a fault, and a caller branches on error"
    assert hit.key_facts["turns_exhausted"] is True
    assert "turns_exhausted" in hit.typed_fields, "a condition may branch only on a typed key"
    assert hit.artefact_refs == ["wt/root"], "the successor continues from exactly this tree"

    # The control: a genuine error with the same is_error flag must still be a failure, or the
    # branch above would be swallowing every error rather than reclassifying one.
    genuine = outcome_from_usage(
        model="sonnet",
        num_turns=3,
        is_error=True,
        text="boom",
        usage=usage,
        first_turn_input=21,
        cwd_rel="wt/root",
        subtype="error_during_execution",
        cost_usd=None,
    )
    assert genuine.status is NodeStatus.FAILED
    assert genuine.error is not None
    assert genuine.key_facts["turns_exhausted"] is False
    assert genuine.artefact_refs == []

    # And the ordinary success path is untouched.
    ok = outcome_from_usage(
        model="sonnet",
        num_turns=3,
        is_error=False,
        text="ok",
        usage=usage,
        first_turn_input=21,
        cwd_rel="wt/root",
        subtype="success",
        cost_usd=None,
    )
    assert ok.status is NodeStatus.DONE
    assert ok.key_facts["turns_exhausted"] is False


# ---------------------------------------------------------------------------------
# Task 7: the cost and the cache-write figure, on every exit path.
#
# The single-agent control this coordinator is benchmarked against reports its spend as
# the CLI's own `total_cost_usd`, so a node record has to carry that same figure or the
# two cannot be compared in one unit. `tokens.in_` is the input TOTAL (input + cache
# creation + cache read), which cannot say how much of it was a cache WRITE, so that
# component is carried beside the total rather than derived from it.
# ---------------------------------------------------------------------------------

_COST_USD = 1.25
"""The terminal message's own ``total_cost_usd`` for the exit-path arms below.

A value no other figure in this file shares, so a cost read off the wrong field, or a
default left standing, is visible rather than coincidentally equal."""

_CACHE_WRITE = 4242
"""The terminal usage's ``cache_creation_input_tokens`` for those arms, distinct from every
other token figure here for the same reason."""


def _terminal_with_cost(*, num_turns: int) -> ResultMessage:
    """A terminal ``ResultMessage`` carrying both figures Task 7 records.

    Kept apart from :func:`_result` so the arms below state their two figures explicitly
    and no existing test's charged-token arithmetic moves under it.
    """
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=num_turns,
        session_id="s1",
        total_cost_usd=_COST_USD,
        usage={"input_tokens": 7, "cache_creation_input_tokens": _CACHE_WRITE, "output_tokens": 3},
        result="ok",
    )


_EXIT_ARMS: dict[str, tuple[list[AssistantMessage], dict[str, Any], list[datetime]]] = {
    "normal": ([_turn(10)], {}, [_STARTED]),
    "handover": (
        [_turn_of_message("m1", 100), _turn_of_message("m2", 200)],  # turn 2 crosses the 150 ceiling
        {"token_cap": 100_000, "handover_at_tokens": 150},
        [_STARTED],
    ),
    "budget": ([_turn(500)], {"token_cap": 100}, [_STARTED]),
    "deadline": ([_turn(1)], {"deadline_s": 10.0}, [_STARTED, _STARTED + timedelta(seconds=11)]),
}
"""Every way :meth:`ClaudeExecutor._run` can end WITH a terminal message, one arm each.

Each entry is the stream to replay, the request fields that select that exit, and the clock
readings the dispatch sees. The fifth exit - no terminal message at all - has no cost to read
and is the control test below, not an arm here."""


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("arm", "expected_status"),
    [
        ("normal", NodeStatus.DONE),
        ("handover", NodeStatus.NEEDS_CONTINUATION),
        ("budget", NodeStatus.FAILED),
        ("deadline", NodeStatus.CANCELLED),
    ],
)
def test_every_exit_path_records_the_dispatch_s_cost_and_its_cache_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arm: str, expected_status: NodeStatus
) -> None:
    """All four terminal exits must stamp both figures, not just the ordinary one.

    The three ceiling paths (handover, budget, deadline) build their own outcome rather than
    translating the SDK's - that is what keeps an interrupted dispatch from reporting itself
    as a plain success - so each of them is a place the two figures can be forgotten
    independently, and a run whose expensive nodes are exactly the ones that hit a ceiling
    would then report a cost of nothing for them.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    turns, overrides, readings = _EXIT_ARMS[arm]
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    FakeStreamClient.configure(turns, _terminal_with_cost(num_turns=len(turns)))
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=_SequenceClock(readings))

    outcome = asyncio.run(executor.run(_request(tmp_path, **overrides)))

    assert outcome.status is expected_status, "the arm did not take the exit path it names"
    assert outcome.cost_usd == _COST_USD
    assert outcome.tokens is not None
    assert outcome.tokens.cache_write == _CACHE_WRITE


@pytest.mark.os_agnostic
def test_a_dispatch_that_never_reached_a_terminal_message_records_no_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The absent case, decided rather than inherited: no terminal message, no cost.

    ``total_cost_usd`` only ever arrives on a ``ResultMessage``, so a stream that ends without
    one has no figure to record and the record must say ``None`` - never ``0.0``, which is a
    real and different statement (a subscription row genuinely costs nothing at the margin).
    ``cache_write`` is 0 there for the same reason every other token field is: the usage
    mapping this path is handed is empty.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", _NoTerminalStreamClient)

    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=100)))

    assert outcome.error is not None
    assert outcome.error.type == "budget_exceeded"  # the path really is the interrupted one
    assert outcome.cost_usd is None
    assert outcome.tokens is not None
    assert outcome.tokens.cache_write == 0


@pytest.mark.os_agnostic
def test_the_tokens_block_carries_cache_creation_separately_from_the_input_total() -> None:
    """``in_`` folds three fields into one number, so cache creation is not recoverable from it.

    50 + 3873 + 119786 has exactly as many decompositions as a reader cares to invent; the
    record has to state the 3873 itself for a cost or cache-effectiveness question to be
    answerable from it at all.
    """
    usage = {
        "input_tokens": 50,
        "cache_creation_input_tokens": 3873,
        "cache_read_input_tokens": 119786,
        "output_tokens": 1034,
    }
    o = outcome_from_usage(
        model="sonnet",
        num_turns=1,
        is_error=False,
        text="ok",
        usage=usage,
        first_turn_input=3923,
        cwd_rel="wt/r",
        cost_usd=None,
    )
    assert o.tokens is not None
    assert o.tokens.in_ == 50 + 3873 + 119786  # unchanged: still the input TOTAL
    assert o.tokens.cache_write == 3873

    # A usage mapping this SDK version did not fill in reads 0, the same default every other
    # field takes at this seam - never None, which is reserved for an executor reporting none.
    empty = outcome_from_usage(
        model="sonnet",
        num_turns=1,
        is_error=False,
        text="ok",
        usage={},
        first_turn_input=0,
        cwd_rel="wt/r",
        cost_usd=None,
    )
    assert empty.tokens is not None
    assert empty.tokens.cache_write == 0
