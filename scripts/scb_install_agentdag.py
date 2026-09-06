# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""Install the agentdag agent type into a SlopCodeBench harness clone.

It copies the agent package in under its real Python names, adds the registration import to
``agents/__init__.py`` without disturbing anything else in that file, writes the arm's
``configs/agents/agentdag.yaml`` with the coordinator ref it was told to install, and prints
the harness commit it patched, so a result can be traced back to the harness it ran on.

Two things about it are deliberate.

* **The agent body ships as data.** ``deploy/slopcodebench/agentdag_scb_agent/`` holds
  ``agent.py.tmpl`` and ``__init__.py.tmpl`` rather than ``.py`` files, because this repo's
  own gate walks every directory: pyright follows ``deploy/`` and reports the ``slop_code``
  imports as missing, and pytest's ``--doctest-modules`` IMPORTS what it collects there and
  fails the whole run at collection. Naming them ``.tmpl`` needs no exclusion in either tool,
  which is the point - an exclusion would blind both to whatever lands there next.
* **Nothing type-checks the agent body, so this compiles it.** Every rendered ``.py`` is put
  through ``py_compile`` in a staging directory before anything is written, and a template
  that does not compile refuses the install whole, rather than leaving a half-installed
  package behind that fails at collection inside the harness.

Everything that can refuse happens before the first write, for the same reason.
"""

from __future__ import annotations

import argparse
import ast
import json
import py_compile
import re
import shutil
import subprocess  # nosec B404 - one `git rev-parse` on a caller-named clone, argv built here
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

__all__ = [
    "SOURCE_ROOT",
    "InstallError",
    "InstallReport",
    "harness_commit",
    "install",
    "main",
    "patch_agents_init",
    "render_agent_config",
    "validate_ref",
]

_SUMMARY = "Install the agentdag agent type into a SlopCodeBench harness clone."

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "deploy" / "slopcodebench"
"""Where the shipped agent package and arm config live in this repo."""

PACKAGE_SOURCE_NAME = "agentdag_scb_agent"
CONFIG_SOURCE_NAME = "agentdag.yaml"
TEMPLATE_SUFFIX = ".tmpl"

REGISTRY_RELATIVE = Path("src/slop_code/agent_runner/agents/__init__.py")
PACKAGE_RELATIVE = Path("src/slop_code/agent_runner/agents/agentdag")
CONFIG_RELATIVE = Path("configs/agents/agentdag.yaml")

AGENT_MODULE = "slop_code.agent_runner.agents.agentdag"
AGENTS_PACKAGE = "slop_code.agent_runner.agents."
EXPORTS = ("AgentdagAgent", "AgentdagConfig")

AGENT_OWNED_SETTINGS = ("credentials.claude_oauth_token_file", "kernel.runs_dir")
"""agentdag settings the agent supplies itself, because only it knows where it put the
keyfile and where it mounted the run store. A config naming one of them would put a second
``--set`` for that key on the command line, which reads as configuration and silently is not."""

VERSION_PLACEHOLDER = "REPLACED-BY-INSTALLER"
VERSION_LINE = f'version: "{VERSION_PLACEHOLDER}"'

GIT_EXECUTABLE = shutil.which("git") or "git"
"""Git resolved from PATH once, the same idiom the rest of this repo uses: a bare name
is searched in the PARENT environment by CreateProcess on Windows, which is not the
environment a child is handed."""

REF_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
"""What a ref may look like. It reaches two places that both constrain it: a ``git+https``
URL and the image tag ``AgentConfigBase.get_image`` builds from it, whose grammar is a
leading alphanumeric or underscore then at most 127 more of alphanumeric, dot, dash and
underscore."""


class InstallError(Exception):
    """Raised when the harness, the source package or the requested ref is not usable."""


@dataclass(frozen=True, slots=True)
class InstallReport:
    """What one install did."""

    harness_commit: str
    agentdag_version: str
    package_dir: Path
    config_path: Path
    registry_changed: bool


@dataclass(frozen=True, slots=True)
class _Plan:
    """Everything an install has validated, before it writes anything."""

    harness: Path
    agentdag_version: str
    harness_commit: str
    package_files: dict[str, str]
    config_text: str
    registry_text: str
    registry_changed: bool


def validate_ref(ref: str) -> str:
    """Return ``ref`` when a git URL and a Docker tag can both carry it.

    Args:
        ref: The agentdag git ref the arm's image installs.

    Returns:
        The ref, unchanged.

    Raises:
        InstallError: The ref is blank, too long, or holds a character one of the two
            cannot carry.

    Examples:
        >>> validate_ref("v1.2.3")
        'v1.2.3'
    """
    if REF_PATTERN.match(ref) is None:
        raise InstallError(
            f"{ref!r} is not usable as a git ref and Docker tag: expected a leading letter, "
            "digit or underscore then at most 127 more of letter, digit, dot, dash, underscore"
        )
    return ref


def patch_agents_init(source: str) -> str:
    """Add the agentdag agent's registration to a harness registry module.

    Two edits, each applied only when it is missing, so a second run changes nothing and a
    half-patched file is completed rather than refused. The file is edited by LINE at
    positions the parsed tree names, never re-rendered from the tree, because re-rendering
    would drop every comment in it.

    Args:
        source: The registry module's current text.

    Returns:
        The patched text, or ``source`` unchanged when both edits were already there.

    Raises:
        InstallError: The module is not the registry this expects - no agent imports, no
            module-level ``__all__`` list, an ``__all__`` entry that is not a string, or
            ``__all__`` declared before the imports.
    """
    tree = ast.parse(source)
    anchor = _first_agent_import(tree)
    assignment = _all_assignment(tree)
    if assignment.lineno < anchor.lineno:
        raise InstallError("the registry declares __all__ before its agent imports; patch it by hand")
    lines = source.splitlines(keepends=True)
    lines = _with_exports(lines, assignment)
    lines = _with_imports(lines, tree)
    patched = "".join(lines)
    try:
        ast.parse(patched)
    except SyntaxError as exc:  # pragma: no cover - an internal invariant, not a user input
        raise InstallError(f"patching the registry produced invalid Python: {exc}") from exc
    return patched


def _first_agent_import(tree: ast.Module) -> ast.ImportFrom:
    """The first ``from slop_code.agent_runner.agents.<x> import ...`` in the module."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(AGENTS_PACKAGE):
            return node
    raise InstallError(f"no {AGENTS_PACKAGE}* import found; this is not the harness agent registry")


def _all_assignment(tree: ast.Module) -> ast.Assign:
    """The module-level ``__all__ = [...]`` assignment."""
    for node in tree.body:
        if not isinstance(node, ast.Assign) or node.col_offset != 0:
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        if not isinstance(node.value, ast.List):
            raise InstallError("__all__ is not a list literal; patch the registry by hand")
        return node
    raise InstallError("no module-level __all__ found; this is not the harness agent registry")


def _export_names(node: ast.List) -> list[str]:
    """Every entry of an ``__all__`` list, refusing one that is not a string literal."""
    names: list[str] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            raise InstallError("__all__ holds an entry that is not a string literal; patch it by hand")
        names.append(element.value)
    return names


def _with_exports(lines: list[str], assignment: ast.Assign) -> list[str]:
    """Rewrite the ``__all__`` block to hold this agent's exports as well, sorted."""
    existing = _export_names(cast("ast.List", assignment.value))
    if set(EXPORTS) <= set(existing):
        return lines
    end = assignment.end_lineno
    if end is None:  # pragma: no cover - every parsed statement carries one
        raise InstallError("__all__ has no end position; patch the registry by hand")
    block = ["__all__ = [\n", *[f'    "{name}",\n' for name in sorted(set(existing) | set(EXPORTS))], "]\n"]
    return [*lines[: assignment.lineno - 1], *block, *lines[end:]]


def _with_imports(lines: list[str], tree: ast.Module) -> list[str]:
    """Insert the missing agentdag imports above the first agent import."""
    missing = [name for name in EXPORTS if name not in _agentdag_imports(tree)]
    if not missing:
        return lines
    anchor = _first_agent_import(tree)
    added = [f"from {AGENT_MODULE} import {name}\n" for name in missing]
    return [*lines[: anchor.lineno - 1], *added, *lines[anchor.lineno - 1 :]]


def _agentdag_imports(tree: ast.Module) -> set[str]:
    """Names the module already imports from the agentdag agent package."""
    return {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == AGENT_MODULE
        for alias in node.names
    }


def render_agent_config(source: str, *, agentdag_version: str) -> str:
    """Fill the arm config's version placeholder in.

    The substitution is on the placeholder's exact line and the result is parsed back, so a
    config whose placeholder was renamed or already filled refuses rather than being written
    out with a version nobody chose.

    Args:
        source: The shipped arm config's text.
        agentdag_version: The coordinator ref this arm runs.

    Returns:
        The config text to write into the harness.

    Raises:
        InstallError: The placeholder line is absent or repeated, the placeholder survives,
            or the result is not the agentdag arm config.
    """
    ref = validate_ref(agentdag_version)
    found = source.count(VERSION_LINE)
    if found != 1:
        raise InstallError(f"the arm config must carry exactly one {VERSION_LINE!r} line to fill in; found {found}")
    rendered = source.replace(VERSION_LINE, f'version: "{ref}"')
    if VERSION_PLACEHOLDER in rendered:
        raise InstallError(f"{VERSION_PLACEHOLDER} still occurs after rendering; the config carries a second copy")
    _require_arm_config(rendered, ref)
    return rendered


def _require_arm_config(rendered: str, ref: str) -> None:
    """Refuse a rendered config that is not this arm's, or does not carry the ref."""
    try:
        parsed = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        raise InstallError(f"the rendered arm config is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InstallError("the rendered arm config is not a mapping")
    document = cast("dict[str, object]", parsed)
    if document.get("type") != "agentdag":
        raise InstallError(f"the arm config declares type {document.get('type')!r}, not 'agentdag'")
    if document.get("version") != ref:
        raise InstallError(f"the rendered arm config carries version {document.get('version')!r}, not {ref!r}")
    _require_no_agent_owned_settings(document.get("settings"))


def _require_no_agent_owned_settings(settings: object) -> None:
    """Refuse a config that sets a key the agent puts on the command line itself."""
    if not isinstance(settings, dict):
        return
    owned = sorted(set(AGENT_OWNED_SETTINGS) & set(cast("dict[str, object]", settings)))
    if owned:
        raise InstallError(
            f"the arm config sets {', '.join(owned)}, which the agent supplies itself; "
            "remove it rather than passing --set twice for one key"
        )


def harness_commit(harness: Path) -> str:
    """Return the commit the harness clone is checked out at.

    Args:
        harness: Root of the clone.

    Returns:
        The full commit sha.

    Raises:
        InstallError: ``harness`` is not a git work tree, or git could not answer.
    """
    done = subprocess.run(  # noqa: S603 - argv built here, no shell  # nosec B603
        [GIT_EXECUTABLE, "-C", str(harness), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise InstallError(f"could not read the harness commit: {done.stderr.strip() or done.returncode}")
    return done.stdout.strip()


def install(*, harness: Path, agentdag_version: str, source: Path | None = None) -> InstallReport:
    """Install the agent package, the arm config and the registration into ``harness``.

    Args:
        harness: Root of the slop-code-bench clone.
        agentdag_version: The coordinator ref the arm's image installs.
        source: Where to read the package and arm config from; the shipped ones by default.

    Returns:
        What was written, and the harness commit it was written onto.

    Raises:
        InstallError: The ref, the harness, the source package or a template is not usable.
            Nothing is written when this is raised.
    """
    root = SOURCE_ROOT if source is None else source
    plan = _planned(harness=harness, agentdag_version=agentdag_version, root=root)
    return _write(plan)


def _planned(*, harness: Path, agentdag_version: str, root: Path) -> _Plan:
    """Validate everything an install needs, writing nothing."""
    ref = validate_ref(agentdag_version)
    registry = harness / REGISTRY_RELATIVE
    if not registry.is_file():
        raise InstallError(f"{registry} is not a file; --harness must name a slop-code-bench clone")
    package_files = _package_files(root / PACKAGE_SOURCE_NAME)
    _require_compilable(package_files)
    config_text = render_agent_config(_read(root / CONFIG_SOURCE_NAME), agentdag_version=ref)
    original = _read(registry)
    patched = patch_agents_init(original)
    return _Plan(
        harness=harness,
        agentdag_version=ref,
        harness_commit=harness_commit(harness),
        package_files=package_files,
        config_text=config_text,
        registry_text=patched,
        registry_changed=patched != original,
    )


def _read(path: Path) -> str:
    """Read one source file, reporting a missing one as a refusal rather than an OSError."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallError(f"could not read {path}: {exc}") from exc


def _package_files(package: Path) -> dict[str, str]:
    """The package's files keyed by the name each takes once installed."""
    if not package.is_dir():
        raise InstallError(f"{package} is not a directory; --source must name the deploy/slopcodebench root")
    files = {_installed_name(path): _read(path) for path in sorted(package.iterdir()) if path.is_file()}
    if not any(name.endswith(".py") for name in files):
        raise InstallError(f"{package} holds no *.py{TEMPLATE_SUFFIX}; it is not the agent package")
    return files


def _installed_name(path: Path) -> str:
    """The name a source file takes inside the harness: ``agent.py.tmpl`` becomes ``agent.py``."""
    if path.name.endswith(TEMPLATE_SUFFIX):
        return path.name[: -len(TEMPLATE_SUFFIX)]
    return path.name


def _require_compilable(files: dict[str, str]) -> None:
    """Compile every rendered ``.py`` in a staging directory, refusing one that does not.

    This is the only check the agent body gets: it ships as data, so neither pyright nor
    pytest in this repo ever reads it.
    """
    with tempfile.TemporaryDirectory() as staging:
        stage = Path(staging)
        for name, text in files.items():
            if not name.endswith(".py"):
                continue
            path = stage / name
            path.write_text(text, encoding="utf-8")
            try:
                py_compile.compile(str(path), cfile=str(stage / f"{name}c"), doraise=True)
            except py_compile.PyCompileError as exc:
                raise InstallError(f"{name} does not compile: {exc}") from exc


def _write(plan: _Plan) -> InstallReport:
    """Perform the writes a validated plan describes."""
    package_dir = plan.harness / PACKAGE_RELATIVE
    package_dir.mkdir(parents=True, exist_ok=True)
    for name, text in plan.package_files.items():
        (package_dir / name).write_text(text, encoding="utf-8")
    config_path = plan.harness / CONFIG_RELATIVE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(plan.config_text, encoding="utf-8")
    if plan.registry_changed:
        (plan.harness / REGISTRY_RELATIVE).write_text(plan.registry_text, encoding="utf-8")
    return InstallReport(
        harness_commit=plan.harness_commit,
        agentdag_version=plan.agentdag_version,
        package_dir=package_dir,
        config_path=config_path,
        registry_changed=plan.registry_changed,
    )


def _print_report(report: InstallReport, *, as_json: bool) -> None:
    """Print what the install did, as a JSON envelope or as lines for a person."""
    if as_json:
        print(  # noqa: T201 - the envelope IS this script's output
            json.dumps(
                {
                    "ok": True,
                    "harness_commit": report.harness_commit,
                    "agentdag_version": report.agentdag_version,
                    "package_dir": str(report.package_dir),
                    "config_path": str(report.config_path),
                    "registry_changed": report.registry_changed,
                }
            )
        )
        return
    print(f"harness commit: {report.harness_commit}")  # noqa: T201 - this IS the report
    print(f"agentdag version: {report.agentdag_version}")  # noqa: T201 - this IS the report
    print(f"package: {report.package_dir}")  # noqa: T201 - this IS the report
    print(f"config: {report.config_path}")  # noqa: T201 - this IS the report
    print(f"registry: {'patched' if report.registry_changed else 'already patched'}")  # noqa: T201


def main(argv: list[str] | None = None) -> int:
    """Install into the harness named on the command line. 0 on success, 2 on a refusal."""
    parser = argparse.ArgumentParser(prog="scb_install_agentdag", description=_SUMMARY)
    parser.add_argument("--harness", required=True, type=Path, help="root of the slop-code-bench clone")
    parser.add_argument("--agentdag-version", required=True, help="agentdag git ref the arm's image installs")
    parser.add_argument("--source", type=Path, default=None, help=f"package source (default: {SOURCE_ROOT})")
    parser.add_argument("--json", action="store_true", help="print a JSON envelope instead of lines")
    args = parser.parse_args(argv)
    as_json = bool(cast("bool", args.json))
    raw_source = cast("Path | None", args.source)
    try:
        report = install(
            harness=Path(str(args.harness)),
            agentdag_version=str(args.agentdag_version),
            source=None if raw_source is None else Path(str(raw_source)),
        )
    except InstallError as exc:
        print(f"scb_install_agentdag: {exc}", file=sys.stderr)  # noqa: T201 - the refusal must be seen
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}))  # noqa: T201 - the envelope on failure too
        return 2
    _print_report(report, as_json=as_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
