"""Tests for the SlopCodeBench installer.

Everything the installer decides is exercised here: the idempotent registry patch, the arm
config it writes, and the compile gate that stands in for the type checker the agent template
cannot reach (it ships as data, so that nothing in this repo walks it as Python).

The harness clone is never touched. Every test builds a synthetic tree with the same shape
under ``tmp_path``.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml
from scb_install_agentdag import (
    SOURCE_ROOT,
    InstallError,
    install,
    patch_agents_init,
    render_agent_config,
    validate_policy,
    validate_ref,
)

pytestmark = pytest.mark.os_agnostic

GIT = shutil.which("git") or "git"

REGISTRY_RELATIVE = Path("src/slop_code/agent_runner/agents/__init__.py")
CONFIG_RELATIVE = Path("configs/agents/agentdag.yaml")
PACKAGE_RELATIVE = Path("src/slop_code/agent_runner/agents/agentdag")

REGISTRY_SOURCE = '''"""Agent implementations for running against checkpoints."""

from __future__ import annotations

import typing as tp

from pydantic import Field

# Import modules for their registration side-effects and public APIs.
from slop_code.agent_runner.agents.claude_code import ClaudeCodeAgent
from slop_code.agent_runner.agents.claude_code import ClaudeCodeConfig
from slop_code.agent_runner.agents.codex import CodexAgent
from slop_code.agent_runner.registry import iter_agent_config_types


def _build_agent_config_type() -> tp.Any:
    configs = tuple(iter_agent_config_types())
    if not configs:
        raise RuntimeError("No agent configurations have been registered.")
    union_type = tp.Union[*configs]  # type: ignore[arg-type]
    return tp.Annotated[union_type, Field(discriminator="type")]


AgentConfigType = _build_agent_config_type()

__all__ = [
    "AgentConfigType",
    "ClaudeCodeAgent",
    "ClaudeCodeConfig",
    "CodexAgent",
]
'''

SHA = "0123456789abcdef0123456789abcdef01234567"


def make_harness(root: Path, *, registry: str = REGISTRY_SOURCE) -> Path:
    """A synthetic harness clone with the registry, a sibling config and one commit."""
    harness = root / "slop-code-bench"
    (harness / REGISTRY_RELATIVE.parent).mkdir(parents=True)
    (harness / REGISTRY_RELATIVE).write_text(registry, encoding="utf-8")
    (harness / "configs" / "agents").mkdir(parents=True)
    (harness / "configs" / "agents" / "claude_code.yaml").write_text("type: claude_code\n", encoding="utf-8")
    git(harness, "init")
    git(harness, "add", "-A")
    git(harness, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-m", "seed")
    return harness


def git(repo: Path, *args: str) -> str:
    """Run one git command in ``repo`` and return its stdout."""
    done = subprocess.run(  # noqa: S603 - argv built here, no shell
        [GIT, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return done.stdout.strip()


def tree_of(root: Path) -> dict[str, str]:
    """Every file under ``root`` (excluding git's own store) as relative path to text."""
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        files[str(path.relative_to(root))] = path.read_text(encoding="utf-8", errors="replace")
    return files


def all_names(source: str) -> list[str]:
    """The ``__all__`` entries of a module's source, in file order."""
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.List):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            return [
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
    raise AssertionError("no __all__ in the source")


def imported_names(source: str) -> set[str]:
    """Every name imported from the installed agent package."""
    module = "slop_code.agent_runner.agents.agentdag"
    return {
        alias.name
        for node in ast.parse(source).body
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def test_patch_adds_the_imports_and_the_exports() -> None:
    patched = patch_agents_init(REGISTRY_SOURCE)
    assert imported_names(patched) == {"AgentdagAgent", "AgentdagConfig"}
    assert {"AgentdagAgent", "AgentdagConfig"} <= set(all_names(patched))
    ast.parse(patched)


def test_patch_keeps_the_registry_valid_and_keeps_every_existing_export() -> None:
    patched = patch_agents_init(REGISTRY_SOURCE)
    assert set(all_names(REGISTRY_SOURCE)) < set(all_names(patched))
    assert all_names(patched) == sorted(all_names(patched))


def test_patch_is_a_no_op_on_a_second_run() -> None:
    once = patch_agents_init(REGISTRY_SOURCE)
    assert once != REGISTRY_SOURCE, "the first patch changed nothing, so the second proves nothing"
    assert patch_agents_init(once) == once


def test_patch_completes_a_file_that_has_the_imports_but_not_the_exports() -> None:
    half = REGISTRY_SOURCE.replace(
        "from slop_code.agent_runner.agents.claude_code import ClaudeCodeAgent",
        "from slop_code.agent_runner.agents.agentdag import AgentdagAgent\n"
        "from slop_code.agent_runner.agents.agentdag import AgentdagConfig\n"
        "from slop_code.agent_runner.agents.claude_code import ClaudeCodeAgent",
    )
    patched = patch_agents_init(half)
    assert imported_names(patched) == {"AgentdagAgent", "AgentdagConfig"}
    assert {"AgentdagAgent", "AgentdagConfig"} <= set(all_names(patched))


def test_patch_completes_a_file_that_has_the_exports_but_not_the_imports() -> None:
    half = REGISTRY_SOURCE.replace(
        '    "AgentConfigType",',
        '    "AgentConfigType",\n    "AgentdagAgent",\n    "AgentdagConfig",',
    )
    patched = patch_agents_init(half)
    assert imported_names(patched) == {"AgentdagAgent", "AgentdagConfig"}
    assert all_names(patched).count("AgentdagAgent") == 1


def test_patch_refuses_a_file_with_no_agent_imports() -> None:
    with pytest.raises(InstallError):
        patch_agents_init('__all__ = [\n    "AgentConfigType",\n]\n')


def test_patch_refuses_a_file_with_no_all() -> None:
    with pytest.raises(InstallError):
        patch_agents_init("from slop_code.agent_runner.agents.codex import CodexAgent\n")


def test_patch_refuses_an_all_holding_something_that_is_not_a_string() -> None:
    source = REGISTRY_SOURCE.replace('    "CodexAgent",', "    CodexAgent,")
    with pytest.raises(InstallError):
        patch_agents_init(source)


def test_render_writes_the_version_and_leaves_no_placeholder() -> None:
    rendered = render_agent_config((SOURCE_ROOT / "agentdag.yaml").read_text(encoding="utf-8"), agentdag_version=SHA)
    assert "REPLACED-BY-INSTALLER" not in rendered
    assert yaml.safe_load(rendered)["version"] == SHA


def test_render_refuses_a_source_carrying_no_placeholder() -> None:
    with pytest.raises(InstallError):
        render_agent_config('type: agentdag\nversion: "abc"\n', agentdag_version=SHA)


@pytest.mark.parametrize("owned", ["credentials.claude_oauth_token_file", "kernel.runs_dir"])
def test_render_refuses_a_config_that_sets_a_key_the_agent_supplies_itself(owned: str) -> None:
    """Two --set flags for one key is a silent trap; the install is where it can still be loud."""
    source = (SOURCE_ROOT / "agentdag.yaml").read_text(encoding="utf-8")
    source = source.replace("settings:\n", f"settings:\n  {owned}: /elsewhere\n")
    with pytest.raises(InstallError, match=re.escape(owned)):
        render_agent_config(source, agentdag_version=SHA)


@pytest.mark.parametrize("ref", [SHA, "v1.2.3", "main", "task-11_head"])
def test_validate_ref_accepts_a_ref_a_docker_tag_can_carry(ref: str) -> None:
    assert validate_ref(ref) == ref


@pytest.mark.parametrize("ref", ["", "   ", "a b", "refs/heads/main", "head;rm -rf /", "-leading", "x" * 200])
def test_validate_ref_refuses_anything_else(ref: str) -> None:
    with pytest.raises(InstallError):
        validate_ref(ref)


def test_install_writes_the_package_under_its_real_python_names(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    install(harness=harness, agentdag_version=SHA)
    package = harness / PACKAGE_RELATIVE
    assert (package / "agent.py").is_file()
    assert (package / "__init__.py").is_file()
    assert (package / "docker.j2").read_text(encoding="utf-8") == (
        SOURCE_ROOT / "agentdag_scb_agent" / "docker.j2"
    ).read_text(encoding="utf-8")
    assert not list(package.glob("*.tmpl"))


def test_install_compiles_the_shipped_agent_template(tmp_path: Path) -> None:
    """The compile gate is the only check the agent body gets, so it has to actually run."""
    harness = make_harness(tmp_path)
    install(harness=harness, agentdag_version=SHA)
    ast.parse((harness / PACKAGE_RELATIVE / "agent.py").read_text(encoding="utf-8"))


@pytest.mark.parametrize("broken", ["agent.py.tmpl", "support.py.tmpl"])
def test_install_refuses_a_template_that_does_not_compile_and_writes_nothing(tmp_path: Path, broken: str) -> None:
    """Every rendered module is gated, not only the one named agent.

    The body ships as data, so this compile is the whole of the checking either template
    gets in this repo.
    """
    harness = make_harness(tmp_path)
    before = tree_of(harness)
    source = broken_source(tmp_path / "broken", broken=broken)
    expected = re.escape(f"{broken.removesuffix('.tmpl')} does not compile")
    with pytest.raises(InstallError, match=expected):
        install(harness=harness, agentdag_version=SHA, source=source)
    assert tree_of(harness) == before


def test_install_writes_the_support_module_wired_to_the_agent(tmp_path: Path) -> None:
    """The agent's pure half is a separate module, so a half-installed package is a real risk."""
    harness = make_harness(tmp_path)
    install(harness=harness, agentdag_version=SHA)
    package = harness / PACKAGE_RELATIVE
    assert (package / "support.py").is_file()
    agent = ast.parse((package / "agent.py").read_text(encoding="utf-8"))
    relative_imports = {node.module for node in agent.body if isinstance(node, ast.ImportFrom) and node.level == 1}
    assert "support" in relative_imports, "the agent no longer reads its own pure half"


def test_install_writes_the_arm_config_with_the_requested_version(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    install(harness=harness, agentdag_version=SHA)
    written = yaml.safe_load((harness / CONFIG_RELATIVE).read_text(encoding="utf-8"))
    assert written["type"] == "agentdag"
    assert written["version"] == SHA


def test_the_written_config_states_the_three_arm_fairness_settings(tmp_path: Path) -> None:
    """Each of the three would confound the pair, so each is stated where a reader compares."""
    harness = make_harness(tmp_path)
    install(harness=harness, agentdag_version=SHA)
    written = yaml.safe_load((harness / CONFIG_RELATIVE).read_text(encoding="utf-8"))
    assert written["cli_version"] == "2.1.260"
    assert written["settings"]["kernel.permission_mode"] == "bypassPermissions"
    assert written["settings"]["kernel.deny_tools"] == []


def test_install_patches_the_registry_and_reports_the_harness_commit(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    report = install(harness=harness, agentdag_version=SHA)
    patched = (harness / REGISTRY_RELATIVE).read_text(encoding="utf-8")
    assert imported_names(patched) == {"AgentdagAgent", "AgentdagConfig"}
    assert report.harness_commit == git(harness, "rev-parse", "HEAD")
    assert report.registry_changed


def test_a_second_install_leaves_the_registry_byte_identical(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    assert install(harness=harness, agentdag_version=SHA).registry_changed
    once = (harness / REGISTRY_RELATIVE).read_text(encoding="utf-8")
    report = install(harness=harness, agentdag_version=SHA)
    assert (harness / REGISTRY_RELATIVE).read_text(encoding="utf-8") == once
    assert not report.registry_changed


def test_install_refuses_a_harness_with_no_registry(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-harness"
    empty.mkdir()
    with pytest.raises(InstallError):
        install(harness=empty, agentdag_version=SHA)


def test_install_refuses_a_source_holding_no_python_template(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    source = tmp_path / "hollow"
    (source / "agentdag_scb_agent").mkdir(parents=True)
    (source / "agentdag.yaml").write_text((SOURCE_ROOT / "agentdag.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(InstallError):
        install(harness=harness, agentdag_version=SHA, source=source)


def broken_source(root: Path, *, broken: str = "agent.py.tmpl") -> Path:
    """A source package with one template that is not valid Python."""
    package = root / "agentdag_scb_agent"
    package.mkdir(parents=True)
    (package / "agent.py.tmpl").write_text("from .support import launch_command\n", encoding="utf-8")
    (package / "support.py.tmpl").write_text("def launch_command() -> None:\n    return None\n", encoding="utf-8")
    (package / "__init__.py.tmpl").write_text("from .agent import AgentdagAgent\n", encoding="utf-8")
    (package / broken).write_text("def broken(:\n", encoding="utf-8")
    (package / "docker.j2").write_text("FROM scratch\n", encoding="utf-8")
    (root / "agentdag.yaml").write_text(
        textwrap.dedent(
            """\
            type: agentdag
            version: "REPLACED-BY-INSTALLER"
            """
        ),
        encoding="utf-8",
    )
    return root


def a_policy_file(tmp_path: Path) -> Path:
    """Any readable file: --policy validates that it is one, and agentdag parses it later."""
    policy = tmp_path / "arm-tier-policy.yaml"
    policy.write_text("models: []\n", encoding="utf-8")
    return policy


def test_install_without_a_policy_leaves_the_commented_example_alone(tmp_path: Path) -> None:
    """The default is the shipped tier table, and the committed line stays a comment."""
    harness = make_harness(tmp_path)
    report = install(harness=harness, agentdag_version=SHA)
    text = (harness / CONFIG_RELATIVE).read_text(encoding="utf-8")
    assert report.policy is None
    assert yaml.safe_load(text).get("policy") is None
    assert "# policy: /abs/path/to/tier-policy.yaml" in text


def test_install_with_a_policy_writes_the_resolved_path_over_the_comment(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    policy = a_policy_file(tmp_path)
    report = install(harness=harness, agentdag_version=SHA, policy=policy)
    text = (harness / CONFIG_RELATIVE).read_text(encoding="utf-8")
    assert report.policy == policy.resolve()
    assert yaml.safe_load(text)["policy"] == str(policy.resolve())
    assert "# policy: /abs/path/to/tier-policy.yaml" not in text


def test_install_writes_an_absolute_policy_path_whatever_was_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative path names a different file from another directory; the container gets one path."""
    harness = make_harness(tmp_path)
    a_policy_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    report = install(harness=harness, agentdag_version=SHA, policy=Path("arm-tier-policy.yaml"))
    assert report.policy is not None
    assert report.policy.is_absolute()
    assert yaml.safe_load((harness / CONFIG_RELATIVE).read_text(encoding="utf-8"))["policy"] == str(report.policy)


@pytest.mark.parametrize("missing", ["no-such-file.yaml", "a-directory"])
def test_install_refuses_a_policy_that_is_not_a_readable_file_and_writes_nothing(tmp_path: Path, missing: str) -> None:
    harness = make_harness(tmp_path)
    (tmp_path / "a-directory").mkdir()
    before = tree_of(harness)
    with pytest.raises(InstallError, match="names no readable file"):
        install(harness=harness, agentdag_version=SHA, policy=tmp_path / missing)
    assert tree_of(harness) == before


def test_validate_policy_expands_a_leading_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The launcher's own notes write this path with a ~, and an unexpanded one names nothing."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    policy = a_policy_file(tmp_path)
    assert validate_policy(Path("~") / policy.name) == policy.resolve()


def test_render_refuses_a_source_carrying_no_policy_comment_when_one_is_asked_for(tmp_path: Path) -> None:
    source = (SOURCE_ROOT / "agentdag.yaml").read_text(encoding="utf-8")
    stripped = source.replace("# policy: /abs/path/to/tier-policy.yaml", "# policy: elsewhere")
    with pytest.raises(InstallError, match="exactly one"):
        render_agent_config(stripped, agentdag_version=SHA, policy=tmp_path / "p.yaml")


def test_the_image_builds_under_the_shell_the_arm_actually_runs_under() -> None:
    """A SHELL instruction has to precede the first RUN, or the build asserts the wrong thing.

    The base image declares ``SHELL ["/bin/bash", "-lc"]``. A login bash sources /etc/profile,
    which ASSIGNS PATH rather than extending it, so the template's own ``ENV PATH`` is gone
    inside every RUN - measured in the base image, ``bash -lc`` resolves neither ``claude`` nor
    ``agentdag`` while ``sh -c`` resolves both. The agent spawns its runtime with
    ``disable_setup=True``, so the harness execs ``/bin/sh -c``; matching the build shell to it
    is what makes the template's closing version assertions test the promise the arm depends on.
    Removing the ENV PATH line then fails the build 127, which is how that was checked.
    """
    lines = (SOURCE_ROOT / "agentdag_scb_agent" / "docker.j2").read_text(encoding="utf-8").splitlines()
    directives = [line.split()[0] for line in lines if line[:1] not in {"", "#", " "}]
    assert "SHELL" in directives, "no SHELL instruction; every RUN takes the base image's login bash"
    assert "RUN" in directives, "a template with no RUN would satisfy the ordering vacuously"
    assert directives.index("SHELL") < directives.index("RUN")
