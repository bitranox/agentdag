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
| `token_cap`, `deadline_s`    | counts and seconds, checked at its own turn seam                |

One field carries an assumption rather than a neutral value: ``deny_bash`` presumes the
agent has a shell tool to deny. That is a bound on the KIND of executor (a tool-using
agent), not on the vendor, and it is stated here so the next reading starts from it
rather than rediscovering it.

The field-set assertion is deliberate friction: a field added to the port fails this test,
which is exactly when the reading above has to be redone.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

import pytest

from agentdag.application.kernel.ports import Executor, ExecutorRequest
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
        """
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
    """Fail when the port grows a field, because that is when the reading must be redone."""
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
        "token_cap",
        "deadline_s",
    }
