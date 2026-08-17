"""RED/GREEN tests for the Claude kernel executor: hooks, env allowlist, usage translation.

No SDK process is started here - every assertion is a synthetic hook-input dict, a
temp filesystem, or a hand-built ``usage`` mapping. The SDK is the one genuinely
external edge (M1's own docstring for its adapter says the same); it is exercised by
the M2 probe (``workflow/design/probes/m2-hooks-dontask.md`` in RESEARCH) instead.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from agentdag.adapters.kernel.executor_claude import CredentialCopy, OAuthTokenFile, outcome_from_usage
from agentdag.adapters.kernel.hooks_claude import HookCallback, deny_bash_commands, deny_outside_root


def decision(result: dict[str, Any]) -> str | None:
    specific: dict[str, Any] = result.get("hookSpecificOutput") or {}
    return specific.get("permissionDecision")


def fire(hook: HookCallback, tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Run one hook call the way the SDK would, and return its permission decision."""
    return decision(asyncio.run(hook({"tool_name": tool_name, "tool_input": tool_input}, None, None)))


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
        "USERPROFILE",
        "SYSTEMROOT",
        "TMPDIR",
        "TEMP",
        "TMP",
    }
    src = tmp_path / "creds.json"
    src.write_text('{"t": 1}')
    env2 = CredentialCopy(src).child_env(tmp_path / "node2")
    copy = Path(env2["CLAUDE_CONFIG_DIR"]) / ".credentials.json"
    assert copy.read_text() == '{"t": 1}'
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env2
    copy.write_text("refreshed")
    assert src.read_text() == '{"t": 1}'


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
