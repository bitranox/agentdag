"""Read the pre-registered readings of a SlopCodeBench arm out of a run directory.

`docs/probes/2026-09-04-slopcodebench-control.md` and
`docs/probes/2026-09-05-slopcodebench-corrected-pair.md` pre-register what the arms measure, and
this script is the only thing that computes it, so a number in a write-up is never a number
somebody read off a log by eye.

Three of the quantities are recomputed here rather than taken from the harness, because the
harness reports them per checkpoint and the pre-registration asks for them per ARM:

* ``strict_pass_rate`` - all passed over all tests, INCLUDING prior checkpoints' regression tests
* ``core_pass_rate`` - the explicitly specified behaviours only
* ``isolated_pass_rate`` - this checkpoint's own tests, regression removed

The formulas are the harness's own (``src/slop_code/metrics/checkpoint/extractors.py``), applied
to each checkpoint's ``evaluation.json``.

**The strain reading needs care, and the pre-registration got its name wrong.** It was written as
``input + cache_write``, which is the protocol's NEW-TOKEN cost unit, not context occupancy: a
request's prompt is ``input + cache_read + cache_write``. Both are reported, separately and
labelled, and the write-up says which one answers which question. Occupancy is what decides
whether one agent's context was ever the binding constraint.

**The repair reading is None where it is not measurable, which is not zero.** ``repaired`` asks
how many failures a checkpoint carried in and then cleared, which needs the PREVIOUS checkpoint's
split of its failures into own and inherited, so ``collect_problem`` stamps it rather than
``read_checkpoint``. A checkpoint with no regression suite reports zero inherited failures
whether or not the earlier defects survive, and the first checkpoint of a problem carried nothing
in at all: both read ``None``, and folding either in as a 0 would report a measurement that never
happened.

Three traps in the token accounting, each of which silently moves a total:

* the ``result`` event's usage is the CUMULATIVE dispatch total, so including it in a per-request
  peak reports a context far larger than any single request ever held (measured: 331,679 against
  a true peak of 55,011);
* the CLI repeats one ``message_id`` and its usage once per CONTENT BLOCK, so summing per event
  double counts. Peaks take a max and are unaffected; sums dedupe by ``message_id``;
* the harness records the LAST result event's usage as the checkpoint's. A background task that
  finishes wakes the model for one more turn and emits a fresh ``result``, so a checkpoint with
  several result events reads a single wake-up's tokens off ``inference_result.json`` (measured:
  409 against a stream summing to 180,760). ``new_tokens`` is therefore summed from the stream,
  never read from the record; the record's ``cost`` is the CLI's session-cumulative figure and
  survives the same shape.

**Void condition 3 is read from the stream, not by eye.** A CLI process that hits its turn bound
with work in flight leaves two marks: the harness retries with ``--continue``, which REPLACES the
stream with the retry's own (so the harness's step count outruns the messages left in it), and
the retry's first events report every background task the exited process stranded as "Orphaned
by a previous Claude Code process exit". Either mark, or an ``error_max_turns`` result where the
stream survived, is ``bound_hit``. A wake-up inside one process is NOT that: its result is a
``success`` and nothing is orphaned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "CheckpointReading",
    "CoordinatorReading",
    "ProblemReading",
    "StreamReading",
    "collect_problem",
    "collect_run",
    "peak_prompt_tokens",
    "read_checkpoint",
    "read_coordinator",
    "read_sdk_transcript",
    "read_stream",
]


@dataclass(frozen=True)
class CheckpointReading:
    """One checkpoint's pre-registered readings."""

    problem: str
    checkpoint: str
    strict_pass_rate: float
    core_pass_rate: float
    isolated_pass_rate: float
    total_tests: int
    passed_tests: int
    core_total: int
    core_passed: int
    cost: float
    elapsed: float
    steps: int
    new_tokens: int
    """input + cache_write: the protocol's cost unit, NOT context occupancy."""
    peak_prompt_tokens: int
    """Largest single request's input + cache_read + cache_write: the occupancy reading."""
    had_error: bool
    infrastructure_failure: bool
    failed_own: int
    """Failed tests in groups that ORIGINATED in this checkpoint."""
    failed_inherited: int
    """Failed tests in groups carried forward from an earlier checkpoint."""
    regression_total: int
    """Regression tests this checkpoint was scored against; 0 means it carries no suite."""
    result_events: int
    """``result`` events in the stream: one per process, plus one per background-task wake-up."""
    init_events: int
    """``init`` events in the stream: one per process start or wake-up."""
    orphaned_tasks: int
    """Background tasks a retry reported stranded by the previous process's exit."""
    max_turns_results: int
    """``error_max_turns`` results, present only where a bound-hit process's stream survived."""
    steps_missing_from_stream: int
    """Harness steps beyond the messages left in the stream: a retry replaced the stream.

    Reported for a coordinator too, because it is a real number there, but it is NOT a void
    signal for one: see :attr:`coordinator`.
    """
    coordinator: bool = False
    """Whether this checkpoint was run by the agentdag coordinator rather than one CLI process.

    It changes which evidence void condition 3 is read from. A coordinator has no single CLI
    stream: its steps come from its NODES' transcripts, so ``steps_missing_from_stream`` equals
    the whole step count on every coordinator run and cannot mean what it means for a control.
    """
    chain_exhausted: int = 0
    """Nodes whose CONTINUATION CHAIN ran out, which is void condition 3 for a coordinator.

    The pre-registration's own carve-out: "a node that hands over at 100 turns is NOT this; a node
    whose continuation chain is exhausted is recorded as such". Handing over is ordinary agentdag
    structure, so an ``error_max_turns`` in a node's transcript must never void a checkpoint.
    """
    distinct_agent_messages: int = 0
    """Distinct assistant message ids seen across the evidence, whichever shape it took."""
    repaired: int | None = None
    """Inherited failures cleared since the previous checkpoint, or None where not measurable.

    Stamped by ``collect_problem``, because it depends on the PREVIOUS checkpoint and one
    checkpoint's ``evaluation.json`` cannot supply that.
    """

    @property
    def bound_hit(self) -> bool:
        """Whether void condition 3 fired, read from whichever evidence this arm leaves.

        A COORDINATOR is judged only on an exhausted continuation chain. Its nodes hand over at
        the turn bound by design, so the control's marks are all wrong for it: an
        ``error_max_turns`` is an ordinary handover, and ``steps_missing_from_stream`` is its
        entire step count because it has no single CLI stream at all. Reading a coordinator with
        the control's rule voided every checkpoint of every coordinator run.
        """
        if self.coordinator:
            return self.chain_exhausted > 0
        return self.orphaned_tasks > 0 or self.max_turns_results > 0 or self.steps_missing_from_stream > 0


@dataclass(frozen=True)
class ProblemReading:
    """Every checkpoint of one problem, in checkpoint order."""

    problem: str
    checkpoints: tuple[CheckpointReading, ...]

    @property
    def mean_core_pass_rate(self) -> float | None:
        """The pre-registered ``C``, over checkpoints that HAVE core tests.

        A checkpoint with no Core tests scores 0.0 from the harness's formula, which is
        indistinguishable from failing every Core test. Averaging those in would drag ``C``
        toward a band boundary for a reason that is not about the work, so they are excluded and
        ``None`` is returned when none remain.
        """
        scored = [c.core_pass_rate for c in self.checkpoints if c.core_total > 0]
        return sum(scored) / len(scored) if scored else None

    @property
    def repaired_total(self) -> int:
        """Inherited failures cleared across the checkpoints where the reading is defined."""
        return sum(c.repaired for c in self.checkpoints if c.repaired is not None)

    @property
    def repaired_defined(self) -> int:
        """Checkpoints where a repair count could be observed at all."""
        return sum(1 for c in self.checkpoints if c.repaired is not None)

    @property
    def solved(self) -> int:
        """Checkpoints whose strict pass rate is exactly 1.0 - the pre-registered ``S``."""
        return sum(1 for c in self.checkpoints if c.strict_pass_rate == 1.0)


def _rates(metrics: dict[str, Any]) -> tuple[float, float, float, int, int]:
    """Apply the harness's own pass-rate formulas to one ``evaluation.json``."""
    total_counts: dict[str, int] = metrics.get("total_counts", {})
    pass_counts: dict[str, int] = metrics.get("pass_counts", {})
    total_passed = sum(pass_counts.values())
    total_total = sum(total_counts.values())
    collected = int(metrics.get("pytest_collected", 0) or 0)
    if total_total == 0 and collected:
        total_total = collected
    regression_total = total_counts.get("Regression", 0)
    checkpoint_passed = total_passed - pass_counts.get("Regression", 0)
    checkpoint_total = total_total - regression_total
    core_total = total_counts.get("Core", 0)
    core_passed = pass_counts.get("Core", 0)
    return (
        total_passed / total_total if total_total else 0.0,
        core_passed / core_total if core_total else 0.0,
        checkpoint_passed / checkpoint_total if checkpoint_total else 0.0,
        total_total,
        total_passed,
    )


def _failed_split(metrics: dict[str, Any], *, checkpoint: str) -> tuple[int, int]:
    """Failed tests split into this checkpoint's own and those carried in from earlier ones.

    The harness keys each group ``<origin checkpoint>-<Category>``. Matching the whole prefix
    including the separator keeps ``checkpoint_1`` from claiming ``checkpoint_10``'s groups.
    """
    groups: dict[str, dict[str, list[str]]] = metrics.get("tests", {})
    own = inherited = 0
    for key, group in groups.items():
        failed = len(group.get("failed", []))
        if key.startswith(f"{checkpoint}-"):
            own += failed
        else:
            inherited += failed
    return own, inherited


@dataclass(frozen=True)
class StreamReading:
    """What one checkpoint's ``agent/stdout.jsonl`` says, independent of the harness's record."""

    peak_prompt_tokens: int
    new_tokens: int
    distinct_messages: int
    result_events: int
    init_events: int
    orphaned_tasks: int
    max_turns_results: int


_EMPTY_STREAM = StreamReading(0, 0, 0, 0, 0, 0, 0)
_ORPHANED_MARK = "Orphaned by a previous Claude Code process exit"


def read_stream(stdout_jsonl: Path) -> StreamReading:
    """Fold the CLI's event stream into the readings only it can supply.

    ``result`` events carry the whole dispatch's totals and are excluded from the peak; a repeated
    ``message_id`` (one event per content block) is charged once in ``new_tokens``.
    """
    if not stdout_jsonl.is_file():
        return _EMPTY_STREAM
    peak = new_tokens = results = inits = orphaned = max_turns = 0
    charged: set[str] = set()
    for line in stdout_jsonl.read_text(errors="replace").splitlines():
        try:
            payload: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        event = cast("dict[str, object]", payload)
        kind, subtype = event.get("type"), event.get("subtype")
        results += kind == "result"
        max_turns += kind == "result" and subtype == "error_max_turns"
        inits += kind == "system" and subtype == "init"
        orphaned += kind == "system" and _ORPHANED_MARK in str(event.get("summary", ""))
        usage = _request_usage(event)
        if usage is None:
            continue
        prompt = _int_field(usage, "input_tokens") + _int_field(usage, "cache_read_input_tokens")
        peak = max(peak, prompt + _int_field(usage, "cache_creation_input_tokens"))
        message_id = _message_id(event)
        if message_id in charged:
            continue
        charged.add(message_id)
        new_tokens += _int_field(usage, "input_tokens") + _int_field(usage, "cache_creation_input_tokens")
    return StreamReading(peak, new_tokens, len(charged), results, inits, orphaned, max_turns)


@dataclass(frozen=True)
class CoordinatorReading:
    """What one checkpoint's agentdag run says, when the coordinator ran it instead of one CLI.

    A coordinator checkpoint saves ``agent/runs/<run id>/`` holding the run's ``journal.jsonl``
    and one ``transcript.jsonl`` per node DISPATCH. Those transcripts are the Agent SDK's own
    message objects, not the CLI's event stream, so they need their own fold; see
    :func:`read_sdk_transcript`.
    """

    new_tokens: int
    peak_prompt_tokens: int
    distinct_messages: int
    dispatches: int
    chain_exhausted: int


@dataclass(frozen=True)
class SdkReading:
    """One node dispatch's transcript, folded."""

    new_tokens: int
    peak_prompt_tokens: int
    distinct_messages: int


_SDK_ASSISTANT = "AssistantMessage"
_SDK_RESULT = "ResultMessage"
_CONTINUATION_LIMIT = "continuation_limit"


def read_sdk_transcript(transcript: Path) -> SdkReading:
    """Fold one node dispatch's Agent SDK transcript.

    The two usage semantics differ and mixing them is what made the first attempt at this read a
    peak of 1,874,489 against a 200,000 context window:

    * ``ResultMessage.usage`` is the whole DISPATCH's cumulative total, so it supplies the new
      tokens and must never enter a per-request peak. The control's fold excluded a cumulative
      result by ``type == "result"``, which does not match the SDK's ``"ResultMessage"``.
    * ``AssistantMessage.usage`` is that REQUEST's own, so it supplies the peak. The SDK repeats
      a message per content block exactly as the CLI does, so ids are deduped.

    Verified against the real parity run: summing this over a checkpoint's dispatches reproduces
    the harness's independently recorded ``input + cache_write`` to the token.
    """
    if not transcript.is_file():
        return SdkReading(0, 0, 0)
    new_tokens = peak = 0
    seen: set[str] = set()
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            payload: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        event = cast("dict[str, object]", payload)
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        fields = cast("dict[str, object]", usage)
        kind = event.get("type")
        if kind == _SDK_RESULT:
            new_tokens += _int_field(fields, "input_tokens") + _int_field(fields, "cache_creation_input_tokens")
            continue
        if kind != _SDK_ASSISTANT:
            continue
        message_id = event.get("message_id")
        if isinstance(message_id, str):
            if message_id in seen:
                continue
            seen.add(message_id)
        prompt = _int_field(fields, "input_tokens") + _int_field(fields, "cache_read_input_tokens")
        peak = max(peak, prompt + _int_field(fields, "cache_creation_input_tokens"))
    return SdkReading(new_tokens, peak, len(seen))


def read_coordinator(agent_dir: Path) -> CoordinatorReading | None:
    """Read a coordinator checkpoint's own evidence, or ``None`` when a control ran it.

    Presence of ``agent/runs/<run id>/journal.jsonl`` is the test, rather than absence of the
    control's ``stdout.jsonl``: a missing file is also what a broken or half-saved control
    checkpoint looks like, and those must not be silently reclassified as a coordinator.
    """
    runs = agent_dir / "runs"
    journals = sorted(runs.glob("*/journal.jsonl")) if runs.is_dir() else []
    if not journals:
        return None
    new_tokens = peak = messages = dispatches = 0
    for run in sorted({journal.parent for journal in journals}):
        for transcript in sorted(run.rglob("transcript.jsonl")):
            reading = read_sdk_transcript(transcript)
            new_tokens += reading.new_tokens
            peak = max(peak, reading.peak_prompt_tokens)
            messages += reading.distinct_messages
            dispatches += 1
    return CoordinatorReading(
        new_tokens=new_tokens,
        peak_prompt_tokens=peak,
        distinct_messages=messages,
        dispatches=dispatches,
        chain_exhausted=sum(_chain_exhausted(journal) for journal in journals),
    )


def _chain_exhausted(journal: Path) -> int:
    """Records refused because a node's continuation chain ran out (void condition 3 here)."""
    exhausted = 0
    for line in journal.read_text(errors="replace").splitlines():
        try:
            payload: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        record = cast("dict[str, object]", payload).get("record")
        if not isinstance(record, dict):
            continue
        error = cast("dict[str, object]", record).get("error")
        if isinstance(error, dict):
            exhausted += cast("dict[str, object]", error).get("type") == _CONTINUATION_LIMIT
    return exhausted


def peak_prompt_tokens(stdout_jsonl: Path) -> int:
    """Largest single request's prompt size, excluding the cumulative ``result`` event."""
    return read_stream(stdout_jsonl).peak_prompt_tokens


def _message_id(event: dict[str, object]) -> str:
    """The event's message id, or a per-event token for an event that carries none."""
    message = event.get("message")
    message_id = cast("dict[str, object]", message).get("id") if isinstance(message, dict) else None
    return message_id if isinstance(message_id, str) else f"anonymous:{id(event)}"


def _request_usage(payload: object) -> dict[str, object] | None:
    """The usage block of one request event, or None for anything else (the ``result`` included)."""
    if not isinstance(payload, dict):
        return None
    event = cast("dict[str, object]", payload)
    if event.get("type") == "result":
        return None
    usage = event.get("usage")
    if usage is None:
        message = event.get("message")
        usage = cast("dict[str, object]", message).get("usage") if isinstance(message, dict) else None
    return cast("dict[str, object]", usage) if isinstance(usage, dict) else None


def _int_field(usage: dict[str, object], key: str) -> int:
    """An integer usage field, reading a missing or null field as 0."""
    value = usage.get(key)
    return int(value) if isinstance(value, int) else 0


def read_checkpoint(checkpoint_dir: Path, *, problem: str) -> CheckpointReading | None:
    """Read one checkpoint, or ``None`` when it has not been evaluated yet."""
    evaluation = checkpoint_dir / "evaluation.json"
    inference = checkpoint_dir / "inference_result.json"
    if not evaluation.is_file() or not inference.is_file():
        return None
    metrics: dict[str, Any] = json.loads(evaluation.read_text())
    result: dict[str, Any] = json.loads(inference.read_text())
    strict, core, isolated, total, passed = _rates(metrics)
    total_counts: dict[str, int] = metrics.get("total_counts", {})
    pass_counts: dict[str, int] = metrics.get("pass_counts", {})
    usage: dict[str, Any] = result.get("usage", {})
    stream = read_stream(checkpoint_dir / "agent" / "stdout.jsonl")
    coordinator = read_coordinator(checkpoint_dir / "agent")
    steps = int(usage.get("steps", 0) or 0)
    failed_own, failed_inherited = _failed_split(metrics, checkpoint=checkpoint_dir.name)
    return CheckpointReading(
        problem=problem,
        checkpoint=checkpoint_dir.name,
        strict_pass_rate=strict,
        core_pass_rate=core,
        isolated_pass_rate=isolated,
        total_tests=total,
        passed_tests=passed,
        core_total=total_counts.get("Core", 0),
        core_passed=pass_counts.get("Core", 0),
        cost=float(usage.get("cost", 0.0) or 0.0),
        elapsed=float(result.get("elapsed", 0.0) or 0.0),
        steps=steps,
        new_tokens=stream.new_tokens if coordinator is None else coordinator.new_tokens,
        peak_prompt_tokens=(stream.peak_prompt_tokens if coordinator is None else coordinator.peak_prompt_tokens),
        had_error=bool(result.get("had_error", False)),
        infrastructure_failure=bool(metrics.get("infrastructure_failure", False)),
        failed_own=failed_own,
        failed_inherited=failed_inherited,
        regression_total=total_counts.get("Regression", 0),
        result_events=stream.result_events,
        init_events=stream.init_events,
        orphaned_tasks=stream.orphaned_tasks,
        max_turns_results=stream.max_turns_results,
        steps_missing_from_stream=max(0, steps - _messages(stream, coordinator)),
        coordinator=coordinator is not None,
        chain_exhausted=0 if coordinator is None else coordinator.chain_exhausted,
        distinct_agent_messages=_messages(stream, coordinator),
    )


def _messages(stream: StreamReading, coordinator: CoordinatorReading | None) -> int:
    """Distinct assistant messages, from whichever evidence this checkpoint's arm left."""
    return stream.distinct_messages if coordinator is None else coordinator.distinct_messages


def _checkpoint_number(name: str) -> int | None:
    """The trailing number of ``checkpoint_7``, or None for a name that carries none."""
    tail = name.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _checkpoint_order(path: Path) -> tuple[int, str]:
    """Sort ``checkpoint_10`` after ``checkpoint_9`` rather than beside ``checkpoint_1``."""
    number = _checkpoint_number(path.name)
    return (number if number is not None else 1 << 30, path.name)


def _is_next_after(previous: CheckpointReading, *, current: CheckpointReading) -> bool:
    """Whether ``current`` is the checkpoint immediately after ``previous``."""
    before = _checkpoint_number(previous.checkpoint)
    after = _checkpoint_number(current.checkpoint)
    return before is not None and after is not None and after == before + 1


def _repaired(previous: CheckpointReading | None, *, current: CheckpointReading) -> int | None:
    """Inherited failures cleared at ``current``, or None where nothing could be observed.

    None is not zero, and three cases cannot be observed: the first checkpoint of a problem
    carried nothing in; a checkpoint with no regression suite reports zero inherited failures
    whether or not the earlier defects survive; and where an unevaluated checkpoint was skipped,
    the surviving predecessor is not the one this checkpoint inherited from, so the difference
    between the two spans work nobody scored. A 0 in any of them would report a measurement that
    never happened.
    """
    if previous is None or current.regression_total == 0:
        return None
    if not _is_next_after(previous, current=current):
        return None
    return previous.failed_inherited + previous.failed_own - current.failed_inherited


def _with_repairs(readings: Sequence[CheckpointReading]) -> tuple[CheckpointReading, ...]:
    """Stamp each checkpoint's repair count, which only its predecessor can supply."""
    stamped: list[CheckpointReading] = []
    for reading in readings:
        previous = stamped[-1] if stamped else None
        stamped.append(replace(reading, repaired=_repaired(previous, current=reading)))
    return tuple(stamped)


def collect_problem(problem_dir: Path) -> ProblemReading:
    """Every evaluated checkpoint of one problem, in checkpoint order."""
    dirs = sorted(
        (d for d in problem_dir.iterdir() if d.is_dir() and d.name.startswith("checkpoint_")),
        key=_checkpoint_order,
    )
    readings = [r for d in dirs if (r := read_checkpoint(d, problem=problem_dir.name)) is not None]
    return ProblemReading(problem=problem_dir.name, checkpoints=_with_repairs(readings))


def collect_run(run_dir: Path) -> tuple[ProblemReading, ...]:
    """Every problem in a run directory that has at least one evaluated checkpoint."""
    problems = sorted(d for d in run_dir.iterdir() if d.is_dir() and (d / "problem.yaml").is_file())
    return tuple(p for d in problems if (p := collect_problem(d)).checkpoints)
