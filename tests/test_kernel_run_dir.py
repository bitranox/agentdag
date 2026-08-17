"""RED/GREEN tests for the run directory on disk (design 3.1)."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.domain.models import Decision, RunState, RunStatus

if TYPE_CHECKING:
    from pathlib import Path


def state() -> RunState:
    return RunState(
        run_id="r1", workflow="graph-a", args={}, owner="me", status=RunStatus.RUNNING, policy_version="sha256:p"
    )


def test_create_lays_out_the_run_dir_owner_only_and_refuses_to_reuse_it(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    for rel in ("decisions", "intents", "artefacts", "wt", "nodes", "manifest", "done"):
        assert (rd.root / rel).is_dir()
    if sys.platform != "win32":
        assert rd.root.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        FsRunDir.create(tmp_path, "r1")
    assert FsRunDir.open(tmp_path, "r1").root == rd.root


def test_open_of_a_missing_run_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FsRunDir.open(tmp_path, "no-such-run")


def test_node_dir_is_keyed_by_node_id_and_hash8_and_writes_are_atomic_and_owner_only(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    nd = rd.node_dir("w_migrate@1", "71efdc61")
    assert nd == rd.root / "nodes" / "w_migrate@1" / "71efdc61" and nd.is_dir()
    p = rd.write_atomic("artefacts/x.json", "{}")
    assert p.read_text() == "{}" and not list(rd.root.glob("artefacts/*.tmp*"))
    if sys.platform != "win32":
        assert p.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        rd.node_dir("../escape", "71efdc61")


def test_state_round_trips_and_decisions_are_write_once(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    rd.write_state(state())
    assert rd.read_state() == state()
    assert rd.read_decision("a_push_list") is None
    d = Decision(node_id="a_push_list", decision="hold", by="me", token_id="local")
    rd.write_decision(d)
    assert rd.read_decision("a_push_list") == d
    with pytest.raises(FileExistsError):
        rd.write_decision(d)
    assert json.loads((rd.decisions_dir / "a_push_list.json").read_text())["decision"] == "hold"


def test_write_atomic_refuses_absolute_and_traversal_paths(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    with pytest.raises(ValueError):
        rd.write_atomic("/etc/passwd", "x")
    with pytest.raises(ValueError):
        rd.write_atomic("../escape.json", "x")


def test_marker_intents_dir_artefacts_dir_and_manifest_path(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")

    marker = rd.marker("compact", "w_migrate@1")
    assert marker == rd.root / "done" / "compact" / "w_migrate@1"
    assert (rd.root / "done" / "compact").is_dir()
    assert not marker.exists()  # marker() creates the directory, never the marker file itself

    intents = rd.intents_dir("approve")
    assert intents == rd.root / "intents" / "approve" and intents.is_dir()

    assert rd.artefacts_dir() == rd.root / "artefacts"
    assert rd.manifest_path("m1") == rd.root / "manifest" / "m1.json"
    assert rd.worktree("w1") == rd.root / "wt" / "w1"
    assert not (rd.root / "wt" / "w1").exists()  # worktree() does not create it
