"""The executor port's conformance check: satisfiable without the Claude SDK.

M4 - a second, Codex-backed executor - is cut. The one thing that arm bought and the cut
does NOT take with it is the only empirical evidence that :class:`Executor` is not shaped
around one vendor, and this project has already shipped a port that leaked its domain into
its contract. So the arm is replaced here by its cheap half: an adapter with no model
behind it at all, satisfying the port and driven through it with every
:class:`ExecutorRequest` field named.

Reading the request field by field, asking what a SECOND vendor would pass for each:

| field                        | what a second vendor passes                                    |
|------------------------------|----------------------------------------------------------------|
| `node_dir`, `cwd`            | filesystem paths it already needs                              |
| `isolation_root`             | the root its writes are scanned against                        |
| `brief`, `prompt`            | text; whatever its own base-instructions channel is            |
| `model`                      | a row ALIAS, resolved by our policy table, not a vendor enum    |
| `effort`                     | its own reasoning-effort knob, or `None` where it has none      |
| `max_turns`                  | its agent loop's turn bound                                     |
| `write_set`                  | glob strings; enforced by our scan, not by the vendor           |
| `deny_bash`                  | command patterns to refuse - see below                          |
| `deny_tools`                 | tool names to refuse outright - see below                       |
| `read_roots`                 | directories the node may read inside, or None - see below       |
| `token_cap`, `deadline_s`    | counts and seconds, checked at its own turn seam                |
| `handover_at_tokens`         | a context-window size in tokens - see below                     |
| `is_stopping`                | a plain zero-argument predicate - see below                     |

Two fields carry an assumption rather than a neutral value. ``deny_bash`` presumes the
agent has a shell tool to deny. That is a bound on the KIND of executor (a tool-using
agent), not on the vendor, and it is stated here so the next reading starts from it
rather than rediscovering it.

``deny_tools`` (2026-09-05) carries the same KIND assumption as ``deny_bash`` - a tool-using
agent - plus one the shell list does not: its VALUES are the vendor's own tool names, and the
shipped default (``WebFetch``, ``WebSearch``, ``Task``) names Claude Code's. The field's shape
is neutral (identifiers to refuse), its default is not, and a second vendor's operator lists
that vendor's names in config. Its tolerance rule follows ``read_roots``, not ``token_cap``:
the field exists to bound what a node may REACH, so an adapter that has such tools and cannot
refuse them by name must REFUSE a request carrying a non-empty list rather than run open. An
adapter with no tools at all - this file's ``NonSdkExecutor`` - honours any list vacuously, the
same way it honours ``deny_bash`` with no shell to deny, so it is handed a non-empty one here.

``read_roots`` (2026-09-02) is a neutral VALUE - directories, naming no tool and no vendor
concept - and is the same shape as ``write_set`` pointed the other way, so a second vendor
supplies it as easily. It is less vendor-coupled than ``deny_bash``, which names a shell.
Its assumption is elsewhere: unlike ``token_cap`` and ``deadline_s``, an adapter that
ignores it loses CORRECTNESS rather than a convenience, because the field exists to bound
what a node may see. So the tolerance rule that covers the other optional fields does not
extend here - an adapter that cannot confine reads must REFUSE a request carrying a
non-``None`` ``read_roots`` rather than run it unconfined. ``None`` is the honest value for
such an adapter's callers to pass, and it is what this file's own ``NonSdkExecutor`` is
handed.

``handover_at_tokens`` (design 3.8) presumes two things a second vendor might not offer:
that the adapter can observe its own context size PER TURN while the dispatch is running,
and that it can stop the dispatch at that point. The first is the real bound - a vendor
that reports usage only at the end can enforce a total, never a live window reading. It is
still a neutral VALUE (a token count of the model's window, not a Claude concept), and the
port already tolerates an adapter that cannot honour it: ``None`` means nothing is checked,
which is the same "no bound declared, nothing enforced" rule ``token_cap`` and
``deadline_s`` follow. An adapter that cannot read per-turn context leaves it unchecked and
loses the handover, not correctness.

``is_stopping`` (Task 34) is the most vendor-neutral VALUE of the three - a zero-argument
Python callable returning a bool, carrying no vendor concept at all, which any adapter can
call. What it presumes is the same capability ``handover_at_tokens`` does, and for the same
reason: a channel to intervene in a dispatch that is already running, first to put a notice
in front of the model and then to stop it. A vendor that can only start a dispatch and await
its result cannot honour it, and the port tolerates that identically - the adapter ignores
the predicate, the node runs to its own end, and what is lost is the prompt hand-over, not
correctness. The barrier still reports such a node rather than claiming it finished.

It is a CALLABLE on a record whose other fields are all data, which is worth naming: the
alternative was a second parameter on ``Executor.run``, and that changes the Protocol every
adapter implements in order to carry something most calls leave None. Nothing serialises
this record, so the callable costs nothing here.

The field-set assertion is deliberate friction: a field added to the port fails this test,
which is exactly when the reading above has to be redone.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

import pytest

from agentdag.application.kernel.ports import Executor, ExecutorRequest
from agentdag.domain.kernel_errors import KernelError
from agentdag.domain.models import NodeOutcome, NodeStatus, Tokens

if TYPE_CHECKING:
    from pathlib import Path


class NonSdkExecutor:
    """An executor with no model behind it: it records the request and reports a result.

    Nothing here imports or knows about the Claude agent SDK, which is the whole point -
    the port is satisfied by ordinary Python.
    """

    def __init__(self) -> None:
        """Start with nothing seen."""
        self.seen: list[ExecutorRequest] = []

    async def run(self, request: ExecutorRequest) -> NodeOutcome:
        """Record ``request`` and report a done outcome built from what it carried.

        Args:
            request: The dispatch request.

        Returns:
            A done outcome whose model and charge are the ALIAS the request named, so a
            vendor's own model identifiers never have to reach the record.

        Raises:
            KernelError: the request confines reads and this adapter cannot honour that.
                It has no sandbox and no permission layer, so the only alternative is to
                run the node unconfined, which is the one thing the field exists to
                prevent. Refusing is what the port requires of such an adapter, and this
                is where that requirement stops being prose.
        """
        if request.read_roots is not None:
            raise KernelError(
                "this executor cannot confine reads; refusing a request that declares "
                f"read_roots={request.read_roots!r} rather than running the node unconfined"
            )
        self.seen.append(request)
        return NodeOutcome(
            status=NodeStatus.DONE,
            key_facts={"turns": 1},
            typed_fields=["turns"],
            artefact_refs=[request.cwd.relative_to(request.isolation_root).as_posix()],
            tokens=Tokens(**{"in": 7, "out": 3, "cache_read": 0, "reasoning": None}),
            charged_tokens={request.model: 10},
            executor_used="non-sdk",
            model_used=request.model,
            effort_used=request.effort or "-",
        )


def _request(tmp_path: Path) -> ExecutorRequest:
    """Build a request with EVERY field named, so a new required one breaks construction."""
    cwd = tmp_path / "wt" / "a"
    cwd.mkdir(parents=True)
    return ExecutorRequest(
        node_dir=tmp_path / "nodes" / "w_migrate@0" / "abcd1234",
        cwd=cwd,
        brief="add a line",
        prompt="add a line to the changelog",
        model="mid",
        effort=None,
        max_turns=3,
        isolation_root=tmp_path,
        write_set=("wt/a/**",),
        deny_bash=("rm -rf /",),
        deny_tools=("WebFetch",),  # vacuously honoured: this adapter has no tool by that or any name
        read_roots=None,  # this adapter cannot confine reads, so None is its only honest value
        token_cap=1000,
        deadline_s=30.0,
    )


@pytest.mark.os_agnostic
def test_the_executor_port_is_satisfiable_without_the_claude_sdk(tmp_path: Path) -> None:
    recorder = NonSdkExecutor()
    executor: Executor = recorder  # the protocol match itself is pyright's assertion
    request = _request(tmp_path)

    outcome = asyncio.run(executor.run(request))

    assert recorder.seen == [request]
    assert outcome.status is NodeStatus.DONE
    # The record names the row alias our policy resolved, never a vendor's model id.
    assert outcome.model_used == "mid"
    assert outcome.charged_tokens == {"mid": 10}
    assert outcome.executor_used == "non-sdk"


@pytest.mark.os_agnostic
def test_the_executor_request_carries_no_field_a_second_vendor_could_not_supply() -> None:
    """Fail when the port grows a field, because that is when the reading must be redone.

    ``read_roots`` was added 2026-09-02 and its reading is in this module's docstring,
    beside the others, rather than here - this test is the tripwire, not the record.
    """
    assert {field.name for field in dataclasses.fields(ExecutorRequest)} == {
        "node_dir",
        "cwd",
        "brief",
        "prompt",
        "model",
        "effort",
        "max_turns",
        "isolation_root",
        "write_set",
        "deny_bash",
        "deny_tools",
        "read_roots",
        "token_cap",
        "deadline_s",
        "handover_at_tokens",
        "is_stopping",
    }


@pytest.mark.os_agnostic
def test_an_adapter_that_cannot_confine_reads_refuses_rather_than_running_unconfined(tmp_path: Path) -> None:
    """The port's read-confinement rule, held as a check instead of as a sentence.

    ``read_roots`` is unlike the port's other optional fields: an adapter that ignores
    ``token_cap`` or ``handover_at_tokens`` loses a convenience, while one that ignores this
    loses the guarantee the field exists for, and it loses it SILENTLY - the node runs, the
    record looks ordinary, and nothing anywhere says it was unconfined. So the rule cannot
    live only in the module docstring above.

    The control is the line under it: the same adapter, the same request shape, ``None``
    instead of roots, runs fine. Without that a test asserting a raise would also pass
    against an adapter that refused everything.
    """
    executor = NonSdkExecutor()
    confined = dataclasses.replace(_request(tmp_path), read_roots=(tmp_path / "wt" / "a",))

    with pytest.raises(KernelError, match="cannot confine reads"):
        asyncio.run(executor.run(confined))
    assert executor.seen == []

    assert asyncio.run(executor.run(_request(tmp_path / "second"))).status is NodeStatus.DONE
