"""Design 9 "secrets stay out", the mechanical form: grep a real run dir for known
secret-token prefixes and require zero hits.

The run dir is produced through the Task 13 primitives (a real ``Dispatcher`` over a
real ``FsRunDir``/``JsonlJournal``), dispatching one node whose BRIEF contains a
planted secret and whose FAKE body writes a transcript line containing that same
secret THROUGH the real ``scrub`` function (:mod:`agentdag.domain.scrub` - a pure
domain module; :mod:`agentdag.adapters.kernel.executor_claude` imports it from there,
same as this test does) - so the redaction is exercised, not vacuous (a scrub that
never ran would still pass a test that never gave it anything to redact).

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

# append_transcript is the real wiring test_append_transcript_redacts_a_secret_shaped_value_...
# below drives directly, per this fix round's review, rather than calling scrub() in isolation.
from agentdag.adapters.kernel.executor_claude import append_transcript
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.application.kernel.dispatch import Dispatcher
from agentdag.domain.models import Budget, Isolation, Kind, NodeOutcome, NodeSpec, NodeStatus
from agentdag.domain.scrub import scrub

if TYPE_CHECKING:
    from pathlib import Path

PLANTED = "sk-ant-oat01-PLANTED"
_SECRET_PREFIXES = ("sk-ant-", "oat01-", "ghp_", "pypi-")


@dataclass
class _FakeStreamedMessage:
    """A minimal stand-in for a real streamed SDK message (``AssistantMessage`` etc.).

    A plain ``@dataclass`` so ``_message_to_jsonable``'s ``is_dataclass`` branch - the
    REAL code path ``append_transcript`` uses in production for every streamed SDK
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
    only when its own key matches :data:`~agentdag.domain.scrub.SECRET_KEY_COMPONENTS`
    - proving the KEY pass depends on the key, not just on some value being present (a
    blanket redaction would catch both branches, or neither). ``PLANTED``
    (``sk-ant-oat01-...``) is deliberately NOT used here: since the VALUE pass added
    by this fix round would also catch its SHAPE regardless of key, it cannot isolate
    the KEY pass on its own any more - see
    ``test_append_transcript_redacts_a_secret_shaped_value_under_a_non_secret_key``
    below for that mechanism's own control.
    """
    plain = "internal ticket reference 98765, not a token"
    assert scrub({"note": plain}) == {"note": plain}
    assert scrub({"password": plain}) == {"password": "[scrubbed]"}


@pytest.mark.os_agnostic
def test_scrub_key_pass_usage_token_counts_survive_in_both_spellings() -> None:
    """The bug the FIRST fix round closed: the KEY pass used to match ``token`` as a
    bare substring anywhere in a key, so every usage-accounting field the kernel
    executor streams had its own INTEGER replaced by ``"[scrubbed]"`` in every
    archived ``transcript.jsonl``, permanently destroying the per-turn audit trail a
    reviewer needs to reconstruct a dispatch's spend. That fix round's OWN regression
    (closed by this round): it recognised only the snake_case spelling of those four
    fields, so a streamed SDK message using the camelCase spelling
    (``inputTokens`` etc. - which real transcripts under ``/var/lib/agentdag/runs``
    actually carry) fell back through the component check unprotected from THIS
    direction too, i.e. would have been wrongly redacted by a camelCase-blind
    allowlist. Both spellings of all four fields must survive.

    Mutation check (verified by running it): dropping the
    ``("cache", "read", "input", "tokens")`` entry from
    :data:`~agentdag.domain.scrub.USAGE_COUNT_KEY_ALLOWLIST` makes BOTH
    ``cache_read_input_tokens`` and ``cacheReadInputTokens`` fail this test at once -
    one allowlist entry protects both spellings simultaneously, because
    :func:`~agentdag.domain.scrub._key_components` lowercases and splits both
    spellings down to the identical ``("cache", "read", "input", "tokens")`` tuple.
    """
    usage_snake = {
        "input_tokens": 12,
        "output_tokens": 34,
        "cache_creation_input_tokens": 56,
        "cache_read_input_tokens": 78,
    }
    usage_camel = {
        "inputTokens": 12,
        "outputTokens": 34,
        "cacheCreationInputTokens": 56,
        "cacheReadInputTokens": 78,
    }
    assert scrub({"usage": usage_snake}) == {"usage": usage_snake}  # all four integers survive untouched
    assert scrub({"usage": usage_camel}) == {"usage": usage_camel}  # same fields, camelCase spelling


@pytest.mark.os_agnostic
def test_scrub_key_pass_extended_usage_metadata_keys_survive() -> None:
    """Round two of the same fix, prompted by checking all 138 distinct keys the real
    transcripts under ``/var/lib/agentdag/runs`` carry against the first allowlist:
    six more numeric-or-boolean usage-metadata fields were still being redacted -
    ``max_output_tokens``, ``estimated_tokens``, ``estimated_tokens_delta``,
    ``truncated_by_token_cap``, and the cache-tier breakdown
    ``ephemeral_1h_input_tokens`` / ``ephemeral_5m_input_tokens`` (the only place a
    transcript records which prompt-caching TTL a turn used).

    (A seventh field, ``output_tokens_details``, was added in this same round and
    removed a round later - see ``test_scrub_key_pass_redacts_a_container_valued_key_not_on_the_usage_allowlist``
    below for why a container-valued key gains nothing from being listed here.)

    Four of the six get a both-spellings assertion, same as the original four: the
    alternate spelling (snake_case for a field the SDK emits camelCase, or vice
    versa) is an unambiguous mechanical transform with no digit adjacent to a case
    boundary, so deriving it is not a guess about what the SDK would actually send.
    The two ``ephemeral_*`` fields get ONE assertion each, in the spelling the real
    transcripts actually carry: their names have a digit glued to a letter
    (``1h``, ``5m``), so a synthetic camelCase twin (``ephemeral1hInputTokens``?
    ``ephemeralOneHourInputTokens``?) would be an invented spelling this codebase has
    no evidence the SDK ever sends, not a verified case.

    Mutation check (verified by running it): dropping the
    ``("truncated", "by", "token", "cap")`` entry from
    :data:`~agentdag.domain.scrub.USAGE_COUNT_KEY_ALLOWLIST` makes both
    ``truncatedByTokenCap`` and ``truncated_by_token_cap`` fail together - the same
    one-tuple-covers-both-spellings property the original four fields have, holding
    for a newly added entry too.
    """
    both_spellings = {
        "maxOutputTokens": "max_output_tokens",
        "estimated_tokens": "estimatedTokens",
        "estimated_tokens_delta": "estimatedTokensDelta",
        "truncatedByTokenCap": "truncated_by_token_cap",
    }
    for observed, derived in both_spellings.items():
        assert scrub({observed: 5}) == {observed: 5}, f"{observed!r} should survive"
        assert scrub({derived: 5}) == {derived: 5}, f"{derived!r} should survive"

    for key in ("ephemeral_1h_input_tokens", "ephemeral_5m_input_tokens"):
        assert scrub({key: 5}) == {key: 5}, f"{key!r} should survive"


@pytest.mark.os_agnostic
def test_scrub_key_pass_redacts_a_token_shaped_key_not_on_the_usage_allowlist() -> None:
    """Negative control for the allowlist: ``access_tokens`` is not observed in this
    codebase's own transcripts, but it is a plausible real key elsewhere (an OAuth
    token-exchange response, a stored credentials cache) - and it is picked
    deliberately because it shares the exact TWO-component ``("word", "tokens")``
    shape as the allowlisted ``input_tokens``/``output_tokens`` (a plural noun ending
    in ``tokens``, the same length), unlike ``refresh_token_expires_in`` (which the
    generic secret-word check alone already catches via its singular ``token``
    component, without exercising the allowlist boundary at all). If the allowlist
    matched by SHAPE ("looks like a token count") rather than by exact enumerated
    tuple, this key would slip through; it must not, because it actually names a
    collection of live credentials, not a count.
    """
    assert scrub({"access_tokens": ["hunter2"]}) == {"access_tokens": "[scrubbed]"}
    assert scrub({"accessTokens": ["hunter2"]}) == {"accessTokens": "[scrubbed]"}


@pytest.mark.os_agnostic
def test_scrub_key_pass_still_redacts_every_real_secret_key_shape() -> None:
    """The other half of the same fix: component-bounded matching must not have
    over-corrected into missing a real secret key. A bare secret word, and each as
    one underscore-delimited component of a longer key, must still redact whole.
    """
    for key in ("token", "api_token", "auth_token", "secret", "password", "authorization", "credential"):
        assert scrub({key: "hunter2"}) == {key: "[scrubbed]"}, f"{key!r} should still redact"


@pytest.mark.os_agnostic
def test_scrub_key_pass_redacts_camelcase_secret_keys_and_their_snake_case_twins() -> None:
    """The regression this round fixes: commit 53e81a1 made the KEY pass recognise
    only snake_case component boundaries (a bare non-alphanumeric split), reasoning
    that every real key in this codebase's JSON messages is snake_case. Measured
    against every archived ``transcript.jsonl`` under ``/var/lib/agentdag/runs``, that
    premise was false - the streamed SDK payloads carry camelCase keys throughout
    (``inputTokens``, ``apiKeySource``, ``costUSD``, ...). Under the shipped
    component-bounded-on-separators-only regex, ``{"apiToken": "hunter2"}`` was NOT
    redacted (no separator between ``api`` and ``Token`` for the boundary check to
    bite on), while its snake_case twin ``{"api_token": "hunter2"}`` still was - a
    secret-shape-independent leak for any camelCase-keyed bearer token, session
    token, or access token the SDK happens to emit.

    Mutation check (verified by hand): reverting :func:`~agentdag.domain.scrub._key_components`
    to split on non-alphanumeric separators only makes every ``...Token`` assertion
    below fail (the key is treated as a single unsplit component, e.g.
    ``"apitoken"``, which contains no standalone ``"token"`` component), while the
    ``..._token`` snake_case counterparts keep passing - reproducing exactly the
    asymmetry reported against the shipped code.
    """
    camel_and_snake_pairs = (
        ("apiToken", "api_token"),
        ("authToken", "auth_token"),
        ("accessToken", "access_token"),
        ("sessionToken", "session_token"),
    )
    for camel_key, snake_key in camel_and_snake_pairs:
        assert scrub({camel_key: "hunter2"}) == {camel_key: "[scrubbed]"}, f"{camel_key!r} should redact"
        assert scrub({snake_key: "hunter2"}) == {snake_key: "[scrubbed]"}, f"{snake_key!r} should redact"


@pytest.mark.os_agnostic
def test_scrub_key_pass_redacts_an_acronym_leading_secret_key() -> None:
    """The case a naive camelCase splitter gets wrong: a key that OPENS with an
    acronym run (``APIToken``, ``OAuthToken``) has no lower-to-upper boundary before
    its first letter, so a splitter that only inserts a boundary before every
    uppercase letter would shatter the acronym itself (``APIToken`` -> ``A``, ``P``,
    ``I``, ``Token``) instead of keeping it as one component and only splitting where
    the acronym ends. Either way the ``token``/``secret`` component must still be
    isolated and redacted.

    Mutation check (verified by running it): dropping the acronym-to-word boundary
    pass (:data:`~agentdag.domain.scrub._ACRONYM_TO_WORD_BOUNDARY_RE`) and keeping
    only the lower/digit-to-upper pass makes ``APIToken`` and ``APISecret`` fail this
    test: with no lowercase letter anywhere before ``Token``/``Secret``, the
    lower-to-upper pass alone never fires and the whole key stays one glued component
    (``"apitoken"``), which does not equal the word ``"token"``. ``OAuthToken`` alone
    would NOT catch this regression - it has its own internal lowercase-to-uppercase
    transition (``...Auth`` -> ``Token``) that the lower-to-upper pass catches on its
    own - which is exactly why an acronym-PREFIXED case is required here.
    """
    for key in ("APIToken", "OAuthToken", "APISecret"):
        assert scrub({key: "hunter2"}) == {key: "[scrubbed]"}, f"{key!r} should redact"


@pytest.mark.os_agnostic
def test_scrub_key_pass_redacts_nested_secret_keys_in_dicts_and_lists() -> None:
    """A real transcript line nests secret-keyed fields inside ``tool_input`` dicts
    and, for a tool that takes a list argument, inside list elements too - the KEY
    pass must reach both, camelCase and snake_case alike, at any depth.
    """
    nested = {
        "type": "AssistantMessage",
        "tool_input": {
            "headers": [
                {"Authorization": "Bearer hunter2", "note": "keep me"},
                {"apiToken": "hunter2"},
            ],
            "auth_token": "hunter2",
        },
    }
    result = scrub(nested)
    assert result["tool_input"]["headers"][0]["Authorization"] == "[scrubbed]"
    assert result["tool_input"]["headers"][0]["note"] == "keep me"
    assert result["tool_input"]["headers"][1]["apiToken"] == "[scrubbed]"
    assert result["tool_input"]["auth_token"] == "[scrubbed]"


@pytest.mark.os_agnostic
def test_scrub_key_pass_redacts_a_container_valued_key_not_on_the_usage_allowlist() -> None:
    """``output_tokens_details`` was added to
    :data:`~agentdag.domain.scrub.USAGE_COUNT_KEY_ALLOWLIST` in the round that added
    six other usage-metadata fields, then removed a round later once it became clear
    the entry protected nothing worth protecting: it is a DICT in a real transcript
    (unlike every other entry, which is a scalar int or bool), and this codebase's own
    archived transcripts show no nested keys under it worth naming individually - so
    there was nothing concrete to allowlist, only the container's own shape.

    Removing a key from the allowlist is NOT a no-op the way it might look:
    :data:`~agentdag.domain.scrub.SECRET_KEY_COMPONENTS` still evaluates the key's own
    name independently of what type its value is, and ``output_tokens_details``
    contains the component ``"tokens"`` like every other allowlisted field. Off the
    list, it is redacted WHOLE - the entire dict collapses to the string
    ``"[scrubbed]"`` - same treatment as any other unenumerated token-shaped key, in
    both spellings.

    Mutation check (verified by running it): putting
    ``("output", "tokens", "details")`` back into
    :data:`~agentdag.domain.scrub.USAGE_COUNT_KEY_ALLOWLIST` makes both assertions
    below fail (the dict survives as a walked dict instead of collapsing to a string) -
    pinning that the removal, not just the allowlist's general shape, is what this
    test guards.
    """
    assert scrub({"output_tokens_details": {"reasoning_tokens": 12}}) == {"output_tokens_details": "[scrubbed]"}
    assert scrub({"outputTokensDetails": {"reasoning_tokens": 12}}) == {"outputTokensDetails": "[scrubbed]"}


@pytest.mark.os_agnostic
def test_scrub_key_pass_decides_each_key_independently_at_any_depth() -> None:
    """The leaf-semantics property: the KEY pass decides EACH key on its own, the
    moment ``scrub`` reaches it, never inherited from or short-circuited by an
    ancestor. Nested two levels under an ordinary, unremarkable wrapper key (neither
    secret-shaped nor on the allowlist - ``"details"`` and ``"inner"``, matching the
    real transcript shape ``usage.output_tokens_details.<field>``), a secret-shaped
    sibling key redacts and an allowlisted count sibling survives, side by side, at
    the same depth.

    This already holds, and it was DISCOVERED while reviewing the allowlist, not
    deliberately designed for this case: :func:`~agentdag.domain.scrub.scrub`'s dict
    branch is ``"[scrubbed]" if _is_secret_key(key) else scrub(val)`` - the ELSE
    branch always recurses, for every key that is not itself redacted whole, at every
    depth, regardless of what any ancestor key was. :func:`~agentdag.domain.scrub._is_secret_key`
    is evaluated fresh at each dict level with no memory of the keys above it; there is
    no code path that treats "an ancestor was fine" as "stop checking, or stop
    recursing, from here down". This exact branch shape (redact-whole vs. recurse,
    reapplied at every level, never redact-whole vs. leave-untouched) dates to the
    module's introduction at commit cbe7a62, before the KEY pass, the regression, or
    any allowlist round existed - nobody re-examined it when the allowlist was added,
    it simply falls out of the shape. The module's own VALUE-pass docstring already
    states the intent this depends on ("reached under any key... regardless of its
    key"), also present at cbe7a62, so the walk-never-stops guarantee was a deliberate
    goal from the start - just for the VALUE pass reaching everywhere, not because
    anyone was thinking about a future allowlisted leaf sharing a subtree with a
    secret.

    Mutation check (verified by running it): a plausible ALTERNATIVE implementation -
    one that recurses only ONE level deep and returns anything nested beyond that
    untouched (a refactor that drops the recursive ``scrub(val)`` call in favour of
    the raw ``val`` past the first level) - makes the nested ``apiToken`` assertion
    fail (it survives, two levels down, instead of being redacted), proving this
    depth property is not free with just any dict-walking implementation.
    """
    result = scrub({"details": {"inner": {"apiToken": "hunter2", "input_tokens": 5}}})
    assert result == {"details": {"inner": {"apiToken": "[scrubbed]", "input_tokens": 5}}}


@pytest.mark.os_agnostic
def test_scrub_value_pass_still_redacts_a_secret_shaped_string_under_a_usage_key() -> None:
    """Defense in depth: the KEY pass no longer redacts ``input_tokens`` by name, but
    the VALUE pass never looked at the key at all - a secret-shaped STRING reaching
    that field (never a real usage integer, but ``scrub`` has no way to know that) is
    still caught by :data:`SECRET_TOKEN_SHAPE_RE`, independent of the key fix above.
    """
    assert scrub({"input_tokens": "sk-ant-oat01-LEAKED"}) == {"input_tokens": "[scrubbed]"}


@pytest.mark.os_agnostic
def test_append_transcript_redacts_a_secret_shaped_value_under_a_non_secret_key(tmp_path: Path) -> None:
    """Design 9's other half: ``scrub()``'s VALUE pass catches a token-shaped string
    even under a key ("content") that is not itself named like a secret - driven
    through the REAL wiring (``append_transcript``), not by calling ``scrub()``
    directly, exercising exactly what ``ClaudeExecutor._run`` does with every streamed
    message. This is a non-vacuous mutation-control target: with the VALUE pass
    removed from ``scrub()``, this specific assertion fails (verified by hand at
    fix-round-1 commit time - see the report for both outputs), because "content"
    does not match the KEY pass either.
    """
    planted_shaped = "sk-ant-oat01-PLANTED-0123456789"
    path = tmp_path / "transcript.jsonl"
    append_transcript(path, _FakeStreamedMessage(content=f"leaked: {planted_shaped}"))
    text = path.read_text(encoding="utf-8")
    for prefix in _SECRET_PREFIXES:
        assert prefix not in text, f"{prefix!r} leaked into transcript.jsonl"
    assert "[scrubbed]" in text
    assert '"content"' in text  # sanity: this went through the real dict-key path, not a repr blob
