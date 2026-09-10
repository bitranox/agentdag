"""The abandoned-spend invariant of the SlopCodeBench agent, checked at source.

``deploy/slopcodebench/agentdag_scb_agent/agent.py.tmpl`` cannot be imported here: it needs the
harness package ``slop_code``, which this repo does not depend on, and its sibling
``tests/test_scb_agent_support.py`` explains why the pure half was split out instead. That
leaves ``run`` covered by reading, and one property of it is too costly to leave there.

The property: a coordinator run that dies before it prints its terminal line has still SPENT
tokens, and the harness records what the agent reports. An agent that raises without harvesting
reports cost 0.0, which reads exactly like a cheap checkpoint rather than like a lost figure.
The paid dry run lost 13 minutes of spend that way, unrecoverably, because the run store is a
temporary directory the session removes.

So ``run`` harvests in ONE place - an exception handler every raising path passes through -
rather than at each ``raise``. These tests read the shipped template's AST and fail if that
shape is undone, which is the only failure mode a reader is likely to reintroduce: adding a
``raise`` to the launch path is natural, and forgetting the harvest beside it is silent.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.os_agnostic

AGENT_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "deploy" / "slopcodebench" / "agentdag_scb_agent" / "agent.py.tmpl"
)

HARVEST = "_harvest_abandoned"
"""The method that reads an abandoned run's spend out of the run store."""


def agent_tree() -> ast.Module:
    return ast.parse(AGENT_TEMPLATE.read_text(encoding="utf-8"))


def method(name: str) -> ast.FunctionDef:
    """The named method of the agent class, or a failure naming what was searched."""
    for node in ast.walk(agent_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{AGENT_TEMPLATE.name} defines no method {name}")


def called_methods(node: ast.AST) -> list[str]:
    """Every ``self.<name>(...)`` called anywhere under this node."""
    called: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) and function.value.id == "self":
            called.append(function.attr)
    return called


def sole_try(node: ast.FunctionDef) -> ast.Try:
    tries = [child for child in node.body if isinstance(child, ast.Try)]
    assert len(tries) == 1, f"{node.name} should hold exactly one try block, found {len(tries)}"
    return tries[0]


def test_the_launch_path_can_raise_at_all_so_the_invariant_is_not_vacuous() -> None:
    """A body with no raise would satisfy every assertion below while protecting nothing."""
    raises = [child for child in ast.walk(method("_launch_and_read")) if isinstance(child, ast.Raise)]
    assert len(raises) >= 2, f"the launch path raises {len(raises)} times, so this guard guards little"


def test_run_raises_only_from_its_handler_so_no_path_can_skip_the_harvest() -> None:
    """The whole point: a raise added to the launch path cannot bypass the harvest, because
    ``run`` does not raise on its own account at all - it re-raises what the body raised."""
    run = method("run")
    handler_raises = {id(child) for handler in sole_try(run).handlers for child in ast.walk(handler)}
    outside = [child for child in ast.walk(run) if isinstance(child, ast.Raise) and id(child) not in handler_raises]
    assert outside == [], f"{len(outside)} raise(s) in run() sit outside the handler that harvests"


def test_the_handler_harvests() -> None:
    handlers = sole_try(method("run")).handlers
    assert [HARVEST in called_methods(handler) for handler in handlers] == [True]


def test_nothing_else_harvests_an_abandoned_run_so_a_run_cannot_be_counted_twice() -> None:
    """Before 2026-09-10 the wall-clock path harvested inline, and it was the only path that
    did. One caller means one harvest per attempt, whichever way the attempt died."""
    callers = [
        node.name
        for node in ast.walk(agent_tree())
        if isinstance(node, ast.FunctionDef) and HARVEST in called_methods(node) and node.name != HARVEST
    ]
    assert callers == ["run"]


def test_the_run_store_is_snapshotted_before_the_launch_not_after() -> None:
    """The harvest names the run by set difference, so a snapshot taken after the launch would
    contain the very directory it is looking for and report nothing was abandoned."""
    run = method("run")
    snapshot_lines = [
        child.lineno
        for child in ast.walk(run)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "_run_directories"
    ]
    assert len(snapshot_lines) == 1, "run() should snapshot the run store exactly once"
    assert snapshot_lines[0] < sole_try(run).lineno, "the snapshot must precede the launch"
    assert "_run_directories" not in called_methods(method("_launch_and_read"))
