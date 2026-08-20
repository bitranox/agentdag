"""RED/GREEN tests for the run directory on disk (design 3.1)."""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.domain.kernel_errors import RunRefused
from agentdag.domain.models import Decision, RunState, RunStatus

if TYPE_CHECKING:
    from pathlib import Path


HASH_ONE = "sha256:" + "1" * 64
HASH_TWO = "sha256:" + "2" * 64


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


def test_state_round_trips_and_decisions_are_write_once_per_payload(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    rd.write_state(state())
    assert rd.read_state() == state()
    assert rd.read_decision("a_push_list", HASH_ONE) is None
    d = Decision(node_id="a_push_list", decision="hold", by="me", token_id="local", payload_hash=HASH_ONE)
    rd.write_decision(d)
    assert rd.read_decision("a_push_list", HASH_ONE) == d
    with pytest.raises(FileExistsError):
        rd.write_decision(d)  # write-once for THIS payload
    assert json.loads((rd.decisions_dir / f"a_push_list.{HASH_ONE[7:15]}.json").read_text())["decision"] == "hold"
    # A CHANGED payload is a new question, so its answer is a new file beside the old one,
    # not a refusal: keying write-once on the node id alone is what made a re-approval
    # impossible without deleting a file by hand.
    second = d.model_copy(update={"decision": "approve", "payload_hash": HASH_TWO})
    rd.write_decision(second)
    assert rd.read_decision("a_push_list", HASH_TWO) == second
    assert rd.read_decision("a_push_list", HASH_ONE) == d  # the old answer is still on record


def test_read_decision_refuses_a_file_whose_content_names_a_different_payload(tmp_path: Path) -> None:
    # The short hash in a decision's filename is only 8 hex characters, so a filename match
    # alone is not proof the file answers the payload asked about - the content has to agree.
    rd = FsRunDir.create(tmp_path, "r1")
    d = Decision(node_id="a_push_list", decision="hold", by="me", token_id="local", payload_hash=HASH_ONE)
    rd.write_decision(d)
    stub = rd.decisions_dir / f"a_push_list.{HASH_ONE[7:15]}.json"
    corrupted = d.model_copy(update={"payload_hash": HASH_TWO})
    stub.write_text(corrupted.model_dump_json(indent=1), encoding="utf-8")

    with pytest.raises(RunRefused, match=re.escape(str(stub))):
        rd.read_decision("a_push_list", HASH_ONE)


def test_write_decision_refuses_a_payload_hash_that_could_escape_the_decisions_dir(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    traversal = Decision(node_id="a", decision="hold", by="me", token_id="local", payload_hash="sha256:../../x")
    with pytest.raises(ValueError, match="unsafe payload hash"):
        rd.write_decision(traversal)


def test_list_decisions_returns_every_decision_sorted_and_ignores_reserved_cancel_files(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    first = Decision(node_id="a_push_list", decision="hold", by="me", token_id="local", payload_hash=HASH_ONE)
    second = Decision(node_id="a_push_list", decision="approve", by="me", token_id="local", payload_hash=HASH_TWO)
    rd.write_decision(second)  # written in the order that does NOT match the sort
    rd.write_decision(first)
    (rd.decisions_dir / "a_push_list.cancel.json").write_text('{"node_id": "a_push_list", "reason": "stop"}')
    (rd.decisions_dir / "_run.cancel.json").write_text('{"reason": "stop everything"}')

    listed = rd.list_decisions()

    # HASH_ONE shortens to 11111111 and HASH_TWO to 22222222, so filename order is first, second.
    assert listed == [first, second]


def test_decision_files_reads_identity_from_the_filename_without_opening_it(tmp_path: Path) -> None:
    # decision_files() must not need to PARSE a file to report its identity - that is the
    # whole point: a caller (fold_decisions) can skip an already-folded pair before paying to
    # open it, so a file corrupted AFTER being folded still reports its identity here even
    # though list_decisions()/read_decision_file() would refuse to parse its content.
    rd = FsRunDir.create(tmp_path, "r1")
    d = Decision(node_id="a_push_list", decision="hold", by="me", token_id="local", payload_hash=HASH_ONE)
    rd.write_decision(d)
    stub = rd.decisions_dir / f"a_push_list.{HASH_ONE[7:15]}.json"
    stub.write_text("not json at all")  # corrupted AFTER write_decision published it
    (rd.decisions_dir / "a_push_list.cancel.json").write_text('{"node_id": "a_push_list", "reason": "stop"}')

    refs = rd.decision_files()

    assert len(refs) == 1  # the reserved cancel file is excluded, same as list_decisions()
    assert refs[0].node_id == "a_push_list"
    assert refs[0].short_hash == HASH_ONE[7:15]
    assert refs[0].path == stub
    with pytest.raises(RunRefused):
        rd.read_decision_file(refs[0])  # the identity above did not require parsing it


def test_write_atomic_refuses_absolute_and_traversal_paths(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    with pytest.raises(ValueError):
        rd.write_atomic("/etc/passwd", "x")
    with pytest.raises(ValueError):
        rd.write_atomic("../escape.json", "x")


def test_marker_refuses_a_kind_or_key_that_would_escape_the_run_dir(tmp_path: Path) -> None:
    # An intent's dedup_key is composed by a workflow from data it read off the fleet, and
    # `directory / key` with an ABSOLUTE key discards `directory` outright - so an
    # unvalidated key puts the idempotency marker for a real, irreversible effect outside
    # the run. The plain key beside them is the control: the guard still lets one through.
    rd = FsRunDir.create(tmp_path, "r1")

    assert rd.marker("push", "a.git-" + "a" * 40).parent == rd.root / "done" / "push"

    for bad in ("../../escape", "/etc/passwd", "..", ".", "", "a/b", "a\\b", "a\x00b"):
        with pytest.raises(RunRefused, match="unsafe marker key"):
            rd.marker("push", bad)
        with pytest.raises(RunRefused, match="unsafe marker kind"):
            rd.marker(bad, "k")


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


def test_node_dir_refuses_a_backslash_bearing_node_id(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    with pytest.raises(ValueError):
        rd.node_dir("escape\\node", "71efdc61")


def test_write_decision_and_read_decision_refuse_a_traversal_node_id(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    d = Decision(node_id="../x", decision="hold", by="me", token_id="local", payload_hash=HASH_ONE)
    with pytest.raises(ValueError):
        rd.write_decision(d)
    with pytest.raises(ValueError):
        rd.read_decision("../x", HASH_ONE)


def test_write_atomic_creates_every_missing_parent_level_owner_only(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    p = rd.write_atomic("nodes/x/abcd1234/artefacts/out.json", "{}")
    assert p.read_text() == "{}"
    if sys.platform != "win32":
        for rel in ("nodes/x", "nodes/x/abcd1234", "nodes/x/abcd1234/artefacts"):
            assert (rd.root / rel).stat().st_mode & 0o777 == 0o700


@pytest.mark.os_posix
@pytest.mark.skipif(sys.platform == "win32", reason="an existing directory blocking os.replace is POSIX-specific")
def test_write_atomic_leaves_no_tmp_file_behind_when_the_replace_fails(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    (rd.root / "artefacts" / "blocked.json").mkdir()
    with pytest.raises(OSError):
        rd.write_atomic("artefacts/blocked.json", "{}")
    assert list(rd.root.glob("artefacts/.tmp-*")) == []


def test_read_decision_of_an_empty_stub_file_raises_run_refused_naming_the_path(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    stub = rd.decisions_dir / f"a_push_list.{HASH_ONE[7:15]}.json"
    stub.write_text("")
    with pytest.raises(RunRefused, match=re.escape(str(stub))):
        rd.read_decision("a_push_list", HASH_ONE)
    with pytest.raises(RunRefused, match=re.escape(str(stub))):
        rd.list_decisions()  # the same refusal through the listing, not a silently skipped file


def test_list_decisions_of_a_file_lacking_payload_hash_raises_run_refused_naming_the_path(tmp_path: Path) -> None:
    # payload_hash is required now; a hand-placed file that lacks it is not a valid legacy
    # shape to fall back to any more - it must refuse loudly, naming the file.
    rd = FsRunDir.create(tmp_path, "r1")
    bad = rd.decisions_dir / "a_push_list.11111111.json"
    bad.write_text(json.dumps({"node_id": "a_push_list", "decision": "hold", "by": "me", "token_id": "local"}))

    with pytest.raises(RunRefused, match=re.escape(str(bad))):
        rd.list_decisions()
    with pytest.raises(RunRefused, match=re.escape(str(bad))):
        rd.read_decision("a_push_list", HASH_ONE)


def test_read_state_of_a_corrupt_file_raises_run_refused_naming_the_path(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    rd.write_state(state())
    rd.state_path.write_text("not json")
    with pytest.raises(RunRefused, match=re.escape(str(rd.state_path))):
        rd.read_state()


def test_read_state_of_a_missing_file_still_raises_file_not_found(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    with pytest.raises(FileNotFoundError):
        rd.read_state()


def test_read_text_reads_a_written_file_and_refuses_a_traversal_path(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    rd.write_atomic("nodes/w/abcd1234/brief.md", "the brief")

    assert rd.read_text("nodes/w/abcd1234/brief.md") == "the brief"
    with pytest.raises(FileNotFoundError):
        rd.read_text("nodes/w/abcd1234/missing.md")
    with pytest.raises(ValueError):
        rd.read_text("../escape.md")


def test_write_decision_leaves_no_tmp_file_behind_in_decisions_dir(tmp_path: Path) -> None:
    rd = FsRunDir.create(tmp_path, "r1")
    d = Decision(node_id="a_push_list", decision="hold", by="me", token_id="local", payload_hash=HASH_ONE)
    rd.write_decision(d)
    assert list(rd.decisions_dir.glob(".tmp-*")) == []
