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
from pathlib import Path
from typing import Any

import pytest

# _append_transcript and _input_total are internal helpers this fix round's review
# explicitly asked to be unit-tested directly (owner-only file mode; the three-field
# sum) rather than only indirectly through ClaudeExecutor.run(), which would need a
# real SDK/network call this module's own design keeps out of unit tests.
from agentdag.adapters.kernel.executor_claude import (
    ClaudeExecutor,
    CredentialCopy,
    OAuthTokenFile,
    _append_transcript,  # pyright: ignore[reportPrivateUsage]
    _input_total,  # pyright: ignore[reportPrivateUsage]
    outcome_from_usage,
)
from agentdag.adapters.kernel.hooks_claude import HookCallback, deny_bash_commands, deny_outside_root
from agentdag.application.kernel.ports import ExecutorRequest
from agentdag.domain.errors import KernelError


def decision(result: dict[str, Any]) -> str | None:
    specific: dict[str, Any] = result.get("hookSpecificOutput") or {}
    return specific.get("permissionDecision")


def fire(hook: HookCallback, tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Run one hook call the way the SDK would, and return its permission decision."""
    return decision(asyncio.run(hook({"tool_name": tool_name, "tool_input": tool_input}, None, None)))


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
    hook = deny_outside_root(root)
    assert fire(hook, "Write", {"file_path": str(root / "wt" / "a.py")}) is None
    assert fire(hook, "Write", {"file_path": str(outside)}) == "deny"
    assert fire(hook, "Edit", {"file_path": str(root / "wt" / "link" / "x")}) == "deny"
    assert fire(hook, "Write", {"file_path": str(root / "wt" / ".." / ".." / "escape")}) == "deny"


@pytest.mark.os_agnostic
def test_write_hook_handles_notebookedit_and_fails_closed_with_no_path_key(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "wt").mkdir()
    hook = deny_outside_root(root)
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
    _append_transcript(path, {"note": "hello"})
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert "hello" in path.read_text(encoding="utf-8")


@pytest.mark.os_agnostic
def test_input_total_sums_the_three_input_fields() -> None:
    assert (
        _input_total({"input_tokens": 50, "cache_creation_input_tokens": 3873, "cache_read_input_tokens": 119786})
        == 50 + 3873 + 119786
    )
    assert _input_total({}) == 0
    assert _input_total({"input_tokens": 7}) == 7


@pytest.mark.os_agnostic
def test_options_for_passes_a_validated_effort_and_rejects_an_unknown_one(tmp_path: Path) -> None:
    """``_options_for`` never touches the network, so this is a real-wiring unit test."""
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    options = executor._options_for(_request(tmp_path, effort="high"))  # pyright: ignore[reportPrivateUsage]
    assert options.effort == "high"
    with pytest.raises(KernelError):
        executor._options_for(_request(tmp_path, effort="bogus"))  # pyright: ignore[reportPrivateUsage]


@pytest.mark.os_agnostic
def test_run_never_claims_an_effort_the_dispatch_did_not_run_under(tmp_path: Path) -> None:
    """An invalid effort raises INSIDE ``_options_for`` before any SDK/network call -
    ``run()``'s broad except-Exception guard catches it and must stamp "-", never the
    invalid requested value, since the dispatch never actually ran under it.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    outcome = asyncio.run(executor.run(_request(tmp_path, effort="bogus")))
    assert outcome.status == "failed"
    assert outcome.effort_used == "-"


@pytest.mark.os_agnostic
def test_run_raises_kernel_error_for_a_cwd_outside_the_isolation_root_before_any_dispatch(tmp_path: Path) -> None:
    """A cwd outside the isolation root is a config bug, caught by ``_cwd_rel`` before
    ``ClaudeSDKClient`` is ever constructed - also network-free.
    """
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-SECRET\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    outside_cwd = tmp_path / "elsewhere"
    outside_cwd.mkdir()
    outcome = asyncio.run(executor.run(_request(tmp_path, cwd=outside_cwd)))
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.type == "executor_error"
    assert "KernelError" in outcome.error.message


@pytest.mark.os_agnostic
def test_options_for_blanks_every_non_allowlisted_inherited_variable_under_the_keyfile_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TRUE allowlist, not a secret-name guess: ``SSH_AUTH_SOCK`` and
    ``AWS_ACCESS_KEY_ID`` name neither token/secret/password/authorization/credential,
    so a regex-based blanklist would have let both through - this proves neither does,
    while PATH/HOME and the credential itself survive.
    """
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKEFAKEFAKEFAKE")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "operator-own-value-must-never-leak")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    keyfile = tmp_path / "tok"
    keyfile.write_text("sk-ant-oat01-FROM-KEYFILE\n")
    executor = ClaudeExecutor(OAuthTokenFile(keyfile), deny_bash=())
    request = _request(tmp_path)
    env = executor._options_for(request).env  # pyright: ignore[reportPrivateUsage]
    assert env["SSH_AUTH_SOCK"] == ""
    assert env["AWS_ACCESS_KEY_ID"] == ""
    # The keyfile path carries the NODE's own credential value, not the coordinator's
    # own inherited CLAUDE_CODE_OAUTH_TOKEN - the merge order keeps it, never blanks it.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-FROM-KEYFILE"
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == str(request.node_dir / "home")


@pytest.mark.os_agnostic
def test_options_for_blanks_the_inherited_oauth_token_under_the_credential_copy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy path carries NO ``CLAUDE_CODE_OAUTH_TOKEN`` of its own, so the
    coordinator's own inherited one must be blanked, not merged through.
    """
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "operator-own-value-must-never-leak")
    src = tmp_path / "creds.json"
    src.write_text('{"t": 1}')
    executor = ClaudeExecutor(CredentialCopy(src), deny_bash=())
    env = executor._options_for(_request(tmp_path)).env  # pyright: ignore[reportPrivateUsage]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


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
    )
    assert o.tokens is not None
    assert o.tokens.in_ == 50 + 3873 + 119786
    assert o.charged_tokens == {"sonnet": 50 + 3873 + 119786 + 1034}
    assert o.status == "done"
    bad = outcome_from_usage(
        model="sonnet",
        num_turns=0,
        is_error=True,
        text="Not logged in - Please run /login",
        usage={},
        first_turn_input=0,
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
        cwd_rel="wt/r",
    )
    assert o.status == "failed"
    assert o.error is not None
    assert o.error.type == "executor_error"
    assert o.error.transient is True


@pytest.mark.os_agnostic
def test_result_translation_missing_usage_fields_default_to_zero() -> None:
    o = outcome_from_usage(
        model="sonnet", num_turns=1, is_error=False, text="ok", usage={}, first_turn_input=0, cwd_rel="wt/r"
    )
    assert o.tokens is not None
    assert o.tokens.in_ == 0
    assert o.tokens.out == 0
    assert o.charged_tokens == {"sonnet": 0}
