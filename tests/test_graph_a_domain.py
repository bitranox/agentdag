"""Domain tests for graph A: pure records and pure functions, no I/O."""

from __future__ import annotations

from pathlib import Path

from agentdag.domain.graph_a import (
    PushIntent,
    Tally,
    dedup_key,
    is_scratch_target,
    parse_repos_text,
    reduce_tally,
    stage,
)


def test_parse_repos_text_skips_blank_and_comment_lines() -> None:
    assert parse_repos_text("/a/one\n\n# c\n  /b/two  \n") == [Path("/a/one"), Path("/b/two")]


def test_reduce_tally_counts_and_stage_keeps_only_passed() -> None:
    rows = [
        Tally(repo=Path("/o/a.git"), status="passed", head_sha="a" * 40, test_rc=0),
        Tally(repo=Path("/o/b.git"), status="failed", head_sha="b" * 40, test_rc=2),
        Tally(repo=Path("/o/c.git"), status="work-failed", head_sha="c" * 40, test_rc=None),
    ]
    summary = reduce_tally(rows)
    assert (summary.passed, summary.failed) == (1, 2)
    assert stage(summary) == [PushIntent(repo=Path("/o/a.git"), head_sha="a" * 40, dedup_key="a.git-" + "a" * 40)]


def test_dedup_key_is_name_and_sha() -> None:
    assert dedup_key(Path("/x/repo.git"), "f" * 40) == "repo.git-" + "f" * 40


def test_is_scratch_target(tmp_path: Path) -> None:
    (tmp_path / "origin").mkdir()
    assert is_scratch_target(tmp_path / "origin" / "r.git", tmp_path)
    assert not is_scratch_target(Path("/media/srv-main-softdev/projects/public/libs/x"), tmp_path)
