"""Tests for the ``Notifier`` port, its two sinks, and the four events that reach them.

The operator is the audience for every one of these, so the run is REAL throughout:
graph A over the shipped adapters (``kernel_fakes.launch``), a real crash from
``CommittingExecutor(crash_on=...)``, and the sinks driven through the same seams
production wires. Only the two genuinely external edges are substituted - the model
call, as everywhere in these tests, and the SMTP send, which is injected at
``btx_lib_mail``'s own ``send_notification`` port rather than patched.

The four events have two different emitters, and that is the point of the split below:
``suspended``, ``done`` and ``failed`` are the coordinator's three exits, so the
coordinator emits them; ``crashed`` is the exit that writes nothing, so the deadline
pass detects it instead.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import failing_workflow
import pytest
from failing_workflow import FailingArgs, WorkflowFailedError
from failing_workflow import program as failing_program
from kernel_fakes import CommittingExecutor, RecordingNotifier, decide, launch, policy_path

from agentdag.adapters.email.config import EmailConfig
from agentdag.adapters.graph_a.gate_make import MakeTestGate
from agentdag.adapters.graph_a.git_cli import GitCli
from agentdag.adapters.kernel.clock_utc import UtcClock
from agentdag.adapters.kernel.isolation_scan import IsolationScanner
from agentdag.adapters.kernel.journal_jsonl import JsonlJournal
from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder
from agentdag.adapters.kernel.notify_mail import MailNotifier
from agentdag.adapters.kernel.notify_none import NoNotifier
from agentdag.adapters.kernel.policy_yaml import load_policy
from agentdag.adapters.kernel.run_store_fs import FsRunDir
from agentdag.adapters.kernel.sandbox_none import NoSandbox
from agentdag.application.kernel.crash import CrashOutcome, record_crash
from agentdag.application.kernel.notify import RunEvent
from agentdag.application.kernel.run import run_coordinator
from agentdag.application.workflows import WorkflowDef
from agentdag.domain.keys import hash8
from agentdag.domain.models import RunState, RunStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


AT = "2026-08-21T14:12:03+00:00"


def crashed_run(tmp_path: Path) -> FsRunDir:
    """Leave a run exactly as a killed coordinator leaves one: state ``running``, no result line."""
    with pytest.raises(SystemExit):
        launch(tmp_path, CommittingExecutor(crash_on="w_migrate@1"), parallel=1)
    run_dir = FsRunDir.open(tmp_path / "runs", "r1")
    assert run_dir.read_state().status is RunStatus.RUNNING  # the premise every crash test below rests on
    return run_dir


def sweep(run_dir: FsRunDir, notifier: RecordingNotifier) -> CrashOutcome:
    """Run one deadline-pass crash check over ``run_dir`` with the real lock and clock."""
    return record_crash(run_dir, lock=FileRunLock(), holder=current_holder(), clock=UtcClock(), notifier=notifier)


# --------------------------------------------------------------------------------------
# The coordinator's three exits
# --------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_run_that_suspends_emits_exactly_one_event_and_the_relaunch_that_decides_it_emits_no_second(
    tmp_path: Path,
) -> None:
    # The mid plan's own negative test for this task, and the reason `suspended` is the
    # COORDINATOR's to emit rather than the periodic pass's: a pass over a suspended run
    # would re-send on every tick, and only a run's own arrival at the suspend happens once.
    notifier = RecordingNotifier()
    executor = CommittingExecutor()

    launch(tmp_path, executor, notifier=notifier)

    assert [event.status for event in notifier.events] == [RunStatus.SUSPENDED]

    run_dir = FsRunDir.open(tmp_path / "runs", "r1")
    decide(run_dir, "approve")
    launch(tmp_path, executor, resume="decision", notifier=notifier)

    assert [event.status for event in notifier.events] == [RunStatus.SUSPENDED, RunStatus.DONE]


@pytest.mark.os_agnostic
def test_a_suspend_event_names_the_waiting_node_and_carries_the_payload_s_own_text_and_deadline(
    tmp_path: Path,
) -> None:
    notifier = RecordingNotifier()

    launch(tmp_path, CommittingExecutor(), notifier=notifier)

    event = notifier.events[0]
    run_dir = FsRunDir.open(tmp_path / "runs", "r1")
    state = run_dir.read_state()
    assert event.run_id == "r1"
    assert event.node_id == state.cursor
    # Read back from the payload the decider is actually shown, never retyped here: the
    # event exists to tell an operator what the payload says, so a divergence is the defect.
    assert state.cursor_payload_hash is not None
    payload = json.loads(run_dir.read_text(f"nodes/{state.cursor}/{hash8(state.cursor_payload_hash)}/payload.json"))
    assert event.summary == payload["text"]
    assert event.decide_by == payload["decide_by"]


@pytest.mark.os_agnostic
def test_a_run_that_reaches_the_end_of_its_program_emits_done(tmp_path: Path) -> None:
    notifier = RecordingNotifier()

    launch(tmp_path, CommittingExecutor(), names=[], notifier=notifier)  # an empty fleet halts at discover

    assert [event.status for event in notifier.events] == [RunStatus.DONE]


@pytest.mark.os_agnostic
def test_a_program_that_raises_emits_failed_and_still_lets_the_exception_out(tmp_path: Path) -> None:
    notifier = RecordingNotifier()

    with pytest.raises(WorkflowFailedError):
        _launch_failing_workflow(tmp_path, notifier=notifier)

    assert [event.status for event in notifier.events] == [RunStatus.FAILED]
    assert FsRunDir.open(tmp_path / "runs", "f1").read_state().status is RunStatus.FAILED


@pytest.mark.os_agnostic
def test_a_sink_that_raises_leaves_the_run_s_own_outcome_untouched(tmp_path: Path) -> None:
    # A mail server being down is not a run failure, and a run that finished must not be
    # reported as failed because nobody could be told it finished.
    outcome, run_dir = launch(tmp_path, CommittingExecutor(), names=[], notifier=_RaisingNotifier())

    assert outcome.status is RunStatus.DONE
    assert run_dir.read_state().status is RunStatus.DONE


# --------------------------------------------------------------------------------------
# The exit that writes nothing: the deadline pass detects it
# --------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_a_run_left_running_by_a_dead_coordinator_is_recorded_crashed_and_emitted(tmp_path: Path) -> None:
    run_dir = crashed_run(tmp_path)
    notifier = RecordingNotifier()

    outcome = sweep(run_dir, notifier)

    assert outcome.recorded is True
    assert run_dir.read_state().status is RunStatus.CRASHED
    assert [event.status for event in notifier.events] == [RunStatus.CRASHED]
    assert notifier.events[0].run_id == "r1"


@pytest.mark.os_agnostic
def test_a_second_pass_over_a_run_already_recorded_crashed_emits_nothing(tmp_path: Path) -> None:
    # Recording the state IS the dedup: a periodic pass runs over every run, every tick,
    # and a crashed run keeps its state file forever, so without this it would re-send forever.
    run_dir = crashed_run(tmp_path)
    sweep(run_dir, RecordingNotifier())
    notifier = RecordingNotifier()

    outcome = sweep(run_dir, notifier)

    assert outcome.recorded is False
    assert notifier.events == []


@pytest.mark.os_agnostic
def test_a_run_whose_state_says_running_but_has_journalled_nothing_yet_is_not_called_crashed(
    tmp_path: Path,
) -> None:
    # `run start` writes state=running BEFORE the background coordinator takes the lock,
    # so this shape is a run that is STARTING. The journal is what tells the two apart: the
    # coordinator's first line is appended under the lock, so an empty journal means no
    # coordinator ever got that far, and calling it a crash would mail the operator every
    # time a run starts.
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "starting")
    run_dir.write_state(
        RunState(
            run_id="starting",
            workflow="graph-a",
            args={},
            owner="tester",
            status=RunStatus.RUNNING,
            policy_version="sha256:0",
        )
    )
    notifier = RecordingNotifier()

    outcome = sweep(run_dir, notifier)

    assert outcome.recorded is False
    assert notifier.events == []
    assert run_dir.read_state().status is RunStatus.RUNNING


@pytest.mark.os_agnostic
def test_a_run_whose_coordinator_still_holds_the_lock_is_not_called_crashed(tmp_path: Path) -> None:
    # The lock IS the liveness evidence: a live coordinator holds it for its whole launch,
    # so failing to take it means somebody is running, not that somebody died.
    run_dir = crashed_run(tmp_path)
    lock = FileRunLock()
    token = lock.acquire(run_dir.root, current_holder())
    notifier = RecordingNotifier()
    try:
        outcome = sweep(run_dir, notifier)
    finally:
        lock.release(token)

    assert outcome.recorded is False
    assert notifier.events == []
    assert run_dir.read_state().status is RunStatus.RUNNING


@pytest.mark.os_agnostic
def test_a_run_that_ended_properly_is_never_called_crashed(tmp_path: Path) -> None:
    _, run_dir = launch(tmp_path, CommittingExecutor(), names=[])  # runs to done
    notifier = RecordingNotifier()

    outcome = sweep(run_dir, notifier)

    assert outcome.recorded is False
    assert notifier.events == []
    assert run_dir.read_state().status is RunStatus.DONE


# --------------------------------------------------------------------------------------
# The two sinks
# --------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
def test_the_mail_sink_sends_one_notification_naming_the_run_and_what_happened() -> None:
    sent: list[dict[str, object]] = []

    def send_notification(
        *,
        config: EmailConfig,
        recipients: str | Sequence[str] | None = None,
        subject: str,
        message: str,
        from_address: str | None = None,
    ) -> bool:
        sent.append({"config": config, "recipients": recipients, "subject": subject, "message": message})
        del from_address
        return True

    config = EmailConfig(smtp_hosts=["localhost:25"], from_address="a@example.com", recipients=["op@example.com"])
    sink = MailNotifier(send_notification=send_notification, config=config)

    sink.emit(RunEvent(run_id="r1", workflow="graph-a", status=RunStatus.FAILED, at=AT))

    assert len(sent) == 1
    assert "r1" in str(sent[0]["subject"])
    assert "failed" in str(sent[0]["subject"]).lower()
    assert "graph-a" in str(sent[0]["message"])


@pytest.mark.os_agnostic
def test_the_mail_sink_puts_the_payload_text_and_the_deadline_in_a_suspend_message() -> None:
    sent: list[str] = []

    def send_notification(
        *,
        config: EmailConfig,
        recipients: str | Sequence[str] | None = None,
        subject: str,
        message: str,
        from_address: str | None = None,
    ) -> bool:
        del config, recipients, subject, from_address
        sent.append(message)
        return True

    config = EmailConfig(smtp_hosts=["localhost:25"], from_address="a@example.com", recipients=["op@example.com"])
    sink = MailNotifier(send_notification=send_notification, config=config)

    sink.emit(
        RunEvent(
            run_id="r1",
            workflow="graph-a",
            status=RunStatus.SUSPENDED,
            at=AT,
            node_id="a_push_list",
            summary="push 3 repositories",
            decide_by="2026-08-22T14:12:03+00:00",
        )
    )

    assert "push 3 repositories" in sent[0]
    assert "2026-08-22T14:12:03+00:00" in sent[0]
    assert "a_push_list" in sent[0]


@pytest.mark.os_agnostic
def test_the_no_op_sink_accepts_an_event_and_does_nothing_with_it() -> None:
    # The DEFAULT sink, so its contract is that it cannot fail: an operator who configured
    # no notification must never have a run fail for lack of one.
    NoNotifier().emit(RunEvent(run_id="r1", workflow="graph-a", status=RunStatus.DONE, at=AT))


# --------------------------------------------------------------------------------------
# The event record itself
# --------------------------------------------------------------------------------------


@pytest.mark.os_agnostic
@pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.CANCELLED])
def test_an_event_refuses_a_status_that_is_not_one_of_the_four_notifiable_ones(status: RunStatus) -> None:
    with pytest.raises(ValueError, match="notifiable"):
        RunEvent(run_id="r1", workflow="graph-a", status=status, at=AT)


class _RaisingNotifier:
    """A sink that always fails, standing in for an unreachable mail server."""

    def emit(self, event: RunEvent) -> None:
        """Fail the way a sink whose transport is down fails.

        Raises:
            RuntimeError: always.
        """
        raise RuntimeError(f"no route to host, and the event was {event.status}")


def _launch_failing_workflow(tmp_path: Path, *, notifier: RecordingNotifier) -> None:
    """Run the always-raising workflow over a real run directory."""
    base = tmp_path / "runs"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = FsRunDir.create(base, "f1")
    asyncio.run(
        run_coordinator(
            run_dir=run_dir,
            journal=JsonlJournal(run_dir.journal_path, run_dir.audit_path),
            clock=UtcClock(),
            lock=FileRunLock(),
            holder=current_holder(),
            workflow=WorkflowDef(
                name="failing", args_model=FailingArgs, program=failing_program, module=failing_workflow
            ),
            args=FailingArgs(),
            executors={"claude": CommittingExecutor()},
            gate_port=MakeTestGate(),
            git=GitCli(),
            scanner=IsolationScanner(),
            policy=load_policy(policy_path()),
            sandbox=NoSandbox(),
            parallel=1,
            by="tester",
            token_id="local",
            resume_reason=None,
            notifier=notifier,
        )
    )
