"""Tests for the isolation-root scan: the pure diff/glob judgement and the adapter walk.

``domain.scan`` is pure and takes two already-built manifests; ``IsolationScanner`` is
the one thing that reads the filesystem, over real files under ``tmp_path``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.domain.scan import diff_manifests, stray_paths

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.os_agnostic
def test_scan_flags_a_write_outside_the_write_set_but_not_a_mode_change_or_an_edit_inside(tmp_path: Path) -> None:
    (tmp_path / "wt/a").mkdir(parents=True)
    (tmp_path / "wt/b").mkdir(parents=True)
    (tmp_path / "wt/a/f.py").write_text("x")
    (tmp_path / "wt/b/g.py").write_text("y")
    before = IsolationScanner().snapshot(tmp_path)

    (tmp_path / "wt/a/f.py").write_text("changed")  # inside the write-set
    (tmp_path / "wt/b/g.py").chmod(0o755)  # mode only
    (tmp_path / "wt/b/stray.txt").write_text("tee'd here")  # a sibling worktree: the finding
    (tmp_path / "nodes/w@1/abcd1234").mkdir(parents=True)
    (tmp_path / "nodes/w@1/abcd1234/transcript.jsonl").write_text("{}")  # the node's own dir: allowed
    after = IsolationScanner().snapshot(tmp_path)

    changed = diff_manifests(before, after)

    assert stray_paths(changed, allowed=("wt/a/**", "nodes/w@1/abcd1234/**")) == ["wt/b/stray.txt"]


@pytest.mark.os_agnostic
def test_scan_skips_git_object_churn_and_the_runs_own_control_files(tmp_path: Path) -> None:
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "pack.idx").write_text("git internals")
    (tmp_path / "journal.jsonl").write_text('{"event":"started"}\n')
    (tmp_path / "audit.jsonl").write_text('{"event":"started"}\n')
    (tmp_path / "state.json").write_text("{}")
    (tmp_path / "lock").write_text("holder")
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / "f.py").write_text("x")

    manifest = IsolationScanner().snapshot(tmp_path)

    assert set(manifest) == {"wt/f.py"}
