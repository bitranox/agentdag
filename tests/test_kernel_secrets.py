"""Design 9 "secrets stay out", the mechanical form: grep a real run dir for known
secret-token prefixes and require zero hits.

The run dir is produced through the Task 13 primitives (a real ``Dispatcher`` over a
real ``FsRunDir``/``JsonlJournal``), dispatching one node whose BRIEF contains a
planted secret and whose FAKE body writes a transcript line containing that same
secret THROUGH the real ``scrub`` function :mod:`agentdag.adapters.kernel.executor_claude`
uses - so the redaction is exercised, not vacuous (a scrub that never ran would still
pass a test that never gave it anything to redact).

``scrub`` is two independent passes (see its own docstring): a KEY pass (a value is
redacted when its own key is named like a secret) and a VALUE pass (a string is
redacted wherever it matches a known secret token SHAPE, regardless of its key). Both
are exercised here, separately, with a mutation control each - proving neither
assertion could pass vacuously.

Scope: only ``transcript.jsonl`` and ``record.json`` are asserted secret-free.
``brief.md`` is the operator's own text, written verbatim by the dispatcher, and
legitimately KEEPS the planted secret - it is not scanned here.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.clock_utc import UtcClock

# _append_transcript is the real wiring test_append_transcript_redacts_a_secret_shaped_value_...
# below drives directly, per this fix round's review, rather than calling scrub() in isolation.
from agentdag.adapters.kernel.executor_claude import _append_transcript, scrub  # pyright: ignore[reportPrivateUsage]
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.domain.models import Budget, Isolation, Kind, NodeOutcome, NodeSpec, NodeStatus

if TYPE_CHECKING:
    from pathlib import Path

PLANTED = "sk-ant-oat01-PLANTED"
_SECRET_PREFIXES = ("sk-ant-", "oat01-", "ghp_", "pypi-")


@dataclass
class _FakeStreamedMessage:
    """A minimal stand-in for a real streamed SDK message (``AssistantMessage`` etc.).

    A plain ``@dataclass`` so ``_message_to_jsonable``'s ``is_dataclass`` branch - the
    REAL code path ``_append_transcript`` uses in production for every streamed SDK
    message - renders it as a structured dict with a real ``content`` key, not via
    ``repr()`` (which a plain ``dict`` argument would hit instead, since a dict is not
    itself a dataclass).
    """

    content: str


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
def test_scrub_key_pass_is_selective_not_a_blanket_redaction() -> None:
    """Mutation check for the KEY pass: the SAME non-token-shaped value is redacted
    only when its own key matches ``_SECRET_KEY_RE`` - proving the KEY pass depends on
    the key, not just on some value being present (a blanket redaction would catch
    both branches, or neither). ``PLANTED`` (``sk-ant-oat01-...``) is deliberately NOT
    used here: since the VALUE pass added by this fix round would also catch its
    SHAPE regardless of key, it cannot isolate the KEY pass on its own any more - see
    ``test_append_transcript_redacts_a_secret_shaped_value_under_a_non_secret_key``
    below for that mechanism's own control.
    """
    plain = "internal ticket reference 98765, not a token"
    assert scrub({"note": plain}) == {"note": plain}
    assert scrub({"password": plain}) == {"password": "[scrubbed]"}


@pytest.mark.os_agnostic
def test_append_transcript_redacts_a_secret_shaped_value_under_a_non_secret_key(tmp_path: Path) -> None:
    """Design 9's other half: ``scrub()``'s VALUE pass catches a token-shaped string
    even under a key ("content") that is not itself named like a secret - driven
    through the REAL wiring (``_append_transcript``), not by calling ``scrub()``
    directly, exercising exactly what ``ClaudeExecutor._run`` does with every streamed
    message. This is a non-vacuous mutation-control target: with the VALUE pass
    removed from ``scrub()``, this specific assertion fails (verified by hand at
    fix-round-1 commit time - see the report for both outputs), because "content"
    does not match the KEY pass either.
    """
    planted_shaped = "sk-ant-oat01-PLANTED-0123456789"
    path = tmp_path / "transcript.jsonl"
    _append_transcript(path, _FakeStreamedMessage(content=f"leaked: {planted_shaped}"))
    text = path.read_text(encoding="utf-8")
    for prefix in _SECRET_PREFIXES:
        assert prefix not in text, f"{prefix!r} leaked into transcript.jsonl"
    assert "[scrubbed]" in text
    assert '"content"' in text  # sanity: this went through the real dict-key path, not a repr blob
