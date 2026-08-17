"""Design 9 "secrets stay out", the mechanical form: grep a real run dir for known
secret-token prefixes and require zero hits.

The run dir is produced through the Task 13 primitives (a real ``Dispatcher`` over a
real ``FsRunDir``/``JsonlJournal``), dispatching one node whose BRIEF contains a
planted secret and whose FAKE body writes a transcript line containing that same
secret THROUGH the real ``scrub`` function :mod:`agentdag.adapters.kernel.executor_claude`
uses - so the redaction is exercised, not vacuous (a scrub that never ran would still
pass a test that never gave it anything to redact).

Scope: only ``transcript.jsonl`` and ``record.json`` are asserted secret-free.
``brief.md`` is the operator's own text, written verbatim by the dispatcher, and
legitimately KEEPS the planted secret - it is not scanned here.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.executor_claude import scrub
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.domain.models import Budget, Isolation, Kind, NodeOutcome, NodeSpec, NodeStatus

if TYPE_CHECKING:
    from pathlib import Path

PLANTED = "sk-ant-oat01-PLANTED"
_SECRET_PREFIXES = ("sk-ant-", "oat01-", "ghp_", "pypi-")


def _spec() -> NodeSpec:
    return NodeSpec(
        node_id="n1",
        kind=Kind.WORK,
        executor="claude",
        isolation=Isolation.NONE,
        deadline_s=60.0,
        budget=Budget(),
    )


@pytest.mark.os_agnostic
def test_transcript_and_record_never_carry_a_planted_secret_after_scrub(tmp_path: Path) -> None:
    """See the module docstring for scope: only transcript.jsonl and record.json are checked."""
    runs_base = tmp_path / "runs"
    runs_base.mkdir()
    run_dir = FsRunDir.create(runs_base, "r1")
    journal = JsonlJournal(run_dir.journal_path, run_dir.audit_path)
    dispatcher = Dispatcher.from_journal(journal=journal, run_dir=run_dir, clock=UtcClock())
    seen: list[Path] = []

    async def fake_body(node_dir: Path) -> NodeOutcome:
        seen.append(node_dir)
        # A real streamed message that happens to echo a copy of the credential back
        # (e.g. a tool_input the model constructed from something in its context) -
        # scrubbed exactly as the real ClaudeExecutor.run scrubs every message before
        # it ever reaches disk.
        line = scrub({"type": "AssistantMessage", "tool_input": {"password": f"leaked copy: {PLANTED}"}})
        with (node_dir / "transcript.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        return NodeOutcome(
            status=NodeStatus.DONE,
            artefact_refs=["wt/r"],
            executor_used="claude",
            model_used="sonnet",
            effort_used="-",
        )

    record = asyncio.run(
        dispatcher.dispatch(_spec(), brief=f"do the thing; operator reference {PLANTED}", input_obj={}, body=fake_body)
    )

    assert record.status == NodeStatus.DONE
    node_dir = seen[0]
    brief_text = (node_dir / "brief.md").read_text(encoding="utf-8")
    assert PLANTED in brief_text  # scope check: the brief is the operator's own text, kept verbatim

    transcript_text = (node_dir / "transcript.jsonl").read_text(encoding="utf-8")
    record_text = (node_dir / "record.json").read_text(encoding="utf-8")
    for prefix in _SECRET_PREFIXES:
        assert prefix not in transcript_text, f"{prefix!r} leaked into transcript.jsonl"
        assert prefix not in record_text, f"{prefix!r} leaked into record.json"
    assert "[scrubbed]" in transcript_text


@pytest.mark.os_agnostic
def test_scrub_would_have_failed_if_the_pattern_did_not_match_the_planted_key() -> None:
    """Mutation check: scrub only redacts a value under a MATCHING key, so a key it does
    not recognise leaves the secret in place - proving the fixture above is not vacuously
    green because scrub redacts everything regardless of key.
    """
    untouched = scrub({"note": PLANTED})
    assert untouched == {"note": PLANTED}
    assert re.search("|".join(_SECRET_PREFIXES), json.dumps(untouched))
