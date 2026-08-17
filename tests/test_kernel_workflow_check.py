"""RED/GREEN tests for the workflow determinism check (design 3.3).

A workflow program is coordinator code: it is re-executed on every resume, so a value it
takes from the clock or from randomness would differ between the first run and the replay
and silently change a journal key. The check refuses such a module before anything runs.
"""

from __future__ import annotations

import textwrap
import types

import pytest

from agentdag.application.kernel.workflow_check import assert_deterministic
from agentdag.domain.errors import NondeterministicCallError

FORBIDDEN_CALLS = [
    "time.time()",
    "time.monotonic()",
    "datetime.now()",
    "datetime.datetime.utcnow()",
    "random.random()",
    "uuid.uuid4()",
    "os.urandom(4)",
    "secrets.token_hex()",
]


def module_from(source: str) -> types.ModuleType:
    """Build a module object from source text, carrying that text for the check to read."""
    text = textwrap.dedent(source)
    module = types.ModuleType("wf")
    module.__file__ = "wf.py"
    exec(compile(text, "wf.py", "exec"), module.__dict__)  # noqa: S102 - a throwaway module from the test's own literal
    module.__dict__["__source__"] = text
    return module


@pytest.mark.os_agnostic
@pytest.mark.parametrize("call", FORBIDDEN_CALLS)
def test_a_workflow_reaching_for_the_clock_or_randomness_fails_at_load(call: str) -> None:
    source = (
        "import time, datetime, random, uuid, os, secrets\n"
        "from datetime import datetime as dt\n"
        "def program(co, args):\n"
        f"    return {call}\n"
    )

    with pytest.raises(NondeterministicCallError, match="line 4") as info:
        assert_deterministic(module_from(source))

    assert call.split("(", maxsplit=1)[0] in str(info.value)  # the refusal names the call, not just the line


@pytest.mark.os_agnostic
def test_a_workflow_that_takes_time_from_the_coordinator_loads() -> None:
    module = module_from("def program(co, args):\n    return co.clock.now()\n")

    assert_deterministic(module)  # the coordinator's clock is the sanctioned source: no raise


@pytest.mark.os_agnostic
def test_a_module_with_no_carried_source_is_read_from_disk() -> None:
    from agentdag.adapters.kernel import clock_utc
    from agentdag.domain import keys

    assert_deterministic(keys)  # a pure module passes ...
    with pytest.raises(NondeterministicCallError, match=r"datetime\.now"):
        assert_deterministic(clock_utc)  # ... and the clock ADAPTER is exactly what a workflow may not be
