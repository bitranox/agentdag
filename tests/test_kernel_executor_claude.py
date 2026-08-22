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
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage

# append_transcript and input_total are tested seams this fix round's review asked to
# be unit-tested directly (owner-only file mode; the three-field sum) rather than only
# indirectly through ClaudeExecutor.run(), which would need a real SDK/network call
# this module's own design keeps out of unit tests - both are public, per the same
# review, exactly because they are tested this way.
from agentdag.adapters.kernel import executor_claude as executor_claude_module
from agentdag.adapters.kernel.executor_claude import (
    ClaudeExecutor,
    CredentialCopy,
    OAuthTokenFile,
    allowed_writes,
    append_transcript,
    input_total,
    outcome_from_usage,
)
from agentdag.adapters.kernel.hooks_claude import HookCallback, deny_bash_commands, deny_outside_write_set
from agentdag.application.kernel.ports import ExecutorRequest
from agentdag.domain.kernel_errors import KernelError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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
    options = executor._options_for(_request(tmp_path, effort="high"))  # pyright: ignore[reportPrivateUsage]
    assert options.effort == "high"
    with pytest.raises(KernelError):
        executor._options_for(_request(tmp_path, effort="bogus"))  # pyright: ignore[reportPrivateUsage]


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
        spend_total=987_654,
        cwd_rel="wt/r",
    )
    assert o.tokens is not None
    assert o.tokens.in_ == 50 + 3873 + 119786  # the terminal snapshot: the context at the last turn
    assert o.charged_tokens == {"sonnet": 987_654}  # the dispatch's summed spend, an independent figure
    assert o.status == "done"
    bad = outcome_from_usage(
        model="sonnet",
        num_turns=0,
        is_error=True,
        text="Not logged in - Please run /login",
        usage={},
        first_turn_input=0,
        spend_total=0,
        cwd_rel="wt/r",
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
        spend_total=0,
        cwd_rel="wt/r",
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
        spend_total=0,
        cwd_rel="wt/r",
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
    which is ``input_total(usage) + output_tokens`` per turn - the same two fields
    :func:`outcome_from_usage` sums into ``charged_tokens``.
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
def test_a_dispatch_charges_its_summed_spend_even_at_the_exact_cap_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap boundary, proven against a terminal snapshot that deliberately DISAGREES.

    Two turns whose own totals (``input_total`` + ``output_tokens``) sum to 120, replayed
    against a terminal ``ResultMessage.usage`` totalling 94. The two figures are
    different quantities and this test keeps them different on purpose: a fixture that
    made them equal could not tell which one the record charged, which is how the
    terminal snapshot went unnoticed as the charged figure until a live run measured a
    completed dispatch charging 254465 while the same workload's running total passed
    400000 (``2026-08-22-graph-a-first-live-run/FINDINGS.md`` finding 4).

    At cap=120 the running total lands exactly ON the cap and must NOT interrupt
    (``_on_turn`` uses ``<=``): a node may fully spend the cap it was given. Drop the cap
    to 119 and the SAME running total crosses it, landing ``_budget_outcome`` instead.
    Either way the record charges 120, the figure the cap compared.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    turns = [
        _turn_usage(40, output_tokens=5),  # contributes 45
        _turn_usage(60, output_tokens=5, cache_read_input_tokens=10),  # contributes 75
    ]  # running total after both turns: 120
    terminal = ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id="s1",
        usage={"input_tokens": 90, "output_tokens": 4},  # 94: a snapshot, NOT the 120 sum
        result="ok",
    )
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)

    FakeStreamClient.configure(turns, terminal)
    at_cap = asyncio.run(executor.run(_request(tmp_path, token_cap=120)))
    assert at_cap.status == "done"  # running_total (120) <= cap (120): never interrupted
    assert at_cap.charged_tokens == {"sonnet": 120}  # the sum, not the terminal 94
    assert FakeStreamClient.instances[0].interrupt_calls == 0

    FakeStreamClient.configure(turns, terminal)
    one_under = asyncio.run(executor.run(_request(tmp_path, token_cap=119)))
    assert one_under.status == "failed"
    assert one_under.error is not None
    assert one_under.error.type == "budget_exceeded"
    assert one_under.charged_tokens == {"sonnet": 120}  # the SAME figure, via the interrupted path
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
    assert outcome.charged_tokens == {"sonnet": 500}  # the running total the cap compared, not the terminal 503
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
    first_turn_input, {})`` sitting AFTER the ``async with`` block - reached only when
    the stream closes with no terminal message whatsoever (a connection drop right
    after the interrupted turn, before the SDK's own terminal message would have
    arrived).
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
    ``if cap_hit: return self._budget_outcome(request, first_turn_input, {})`` after
    the ``async with`` block - a capped dispatch whose stream ends with no terminal
    ``ResultMessage`` at all. Both
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
    assert outcome.charged_tokens == {"sonnet": 500}  # no terminal usage arrived, but the turn was still spent
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
    ``_run``'s own ``if deadline_hit: return self._deadline_outcome(request, first_turn_input, {})``
    after the ``async with`` block, reached when the stream ends with no terminal
    ``ResultMessage`` at all.
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
    assert outcome.charged_tokens == {"sonnet": 500}  # no terminal usage arrived, but the turn was still spent
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


@pytest.mark.os_agnostic
def test_a_completed_dispatch_charges_the_summed_spend_not_the_terminal_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the record charges must be the quantity the cap enforces: the SUM across turns.

    Three turns of 80 input tokens each - a running total of 240 - against a terminal
    ``ResultMessage.usage`` reporting 103, the shape a real dispatch produces (the
    terminal usage is one snapshot; the probe measured a completed 12-turn dispatch
    whose terminal figure was 254465 while the same workload's running total passed
    400000, ``2026-08-22-graph-a-first-live-run/FINDINGS.md`` finding 4). The record
    must report 240, because 240 is what
    :meth:`~agentdag.application.kernel.context.Coordinator._run_cap_refusal` adds to
    the NEXT node's cap when it decides whether the run may afford it.

    ``tokens`` keeps the terminal snapshot: that is the context size at the last turn,
    which design 3.8's separate context ceiling reads, and it is a different question
    from what the dispatch spent.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    turns = [_turn(80), _turn(80), _turn(80)]  # running total 240

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=3, usage_input=100))
    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=1_000)))

    assert outcome.status == "done"
    assert FakeStreamClient.instances[0].interrupt_calls == 0
    assert outcome.charged_tokens == {"sonnet": 240}  # the summed spend, not the terminal 103
    assert outcome.tokens is not None
    assert outcome.tokens.in_ == 100  # the terminal snapshot survives, on the field that means it
    assert outcome.tokens.out == 3


@pytest.mark.os_agnostic
def test_a_capped_node_charges_the_running_total_that_tripped_its_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node killed by its cap must charge the figure the cap compared, not a snapshot.

    Three turns of 80 against a cap of 200: the cap fires on the third turn, at a
    running total of 240. The journal has to be able to explain the node's own death -
    a record charging the terminal 103 against a cap of 200 reads as a node killed well
    inside its budget (finding 2 of the same probe).
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    turns = [_turn(80), _turn(80), _turn(80)]

    FakeStreamClient.configure(turns, _result(is_error=False, subtype="success", num_turns=3, usage_input=100))
    outcome = asyncio.run(executor.run(_request(tmp_path, token_cap=200)))

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.type == "budget_exceeded"
    assert outcome.charged_tokens == {"sonnet": 240}  # what _on_turn compared against 200


@pytest.mark.os_agnostic
def test_a_deadline_stopped_node_charges_what_it_spent_before_the_clock_ran_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline path charges the same unit as the cap path: a stopped node still spent.

    The dispatch is interrupted on wall-clock, not tokens, but the tokens it spent
    getting there are charged to the run's row exactly as any other dispatch's are.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    monkeypatch.setattr(executor_claude_module, "ClaudeSDKClient", FakeStreamClient)
    clock = _SequenceClock([_STARTED, _STARTED + timedelta(seconds=11)])
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=(), clock=clock)

    FakeStreamClient.configure([_turn(80)], _result(is_error=False, subtype="success", num_turns=1, usage_input=100))
    outcome = asyncio.run(executor.run(_request(tmp_path, deadline_s=10.0)))

    assert outcome.status == "cancelled"
    assert outcome.error is not None
    assert outcome.error.type == "deadline"
    assert outcome.charged_tokens == {"sonnet": 80}  # the one turn it streamed, not the terminal 103
