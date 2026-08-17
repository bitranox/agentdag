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

# Every evading spelling the reviewer probed and found NOT caught before the alias-map
# fix: an aliased ``import ... as x``, or a ``from ... import`` bringing the name in
# directly, both reaching the same forbidden call through a name the exact-dotted-string
# check in FORBIDDEN_CALLS never spelled out.
EVADING_SPELLINGS = [
    ("from datetime import datetime as dt", "dt.now()"),
    ("import datetime as d", "d.datetime.now()"),
    ("from time import monotonic", "monotonic()"),
    ("from time import time", "time()"),
    ("import random as rnd", "rnd.random()"),
    ("from uuid import uuid4", "uuid4()"),
    ("from secrets import token_hex", "token_hex()"),
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
    source = f"import time, datetime, random, uuid, os, secrets\ndef program(co, args):\n    return {call}\n"

    with pytest.raises(NondeterministicCallError, match="line 3") as info:
        assert_deterministic(module_from(source))

    assert call.split("(", maxsplit=1)[0] in str(info.value)  # the refusal names the call, not just the line


@pytest.mark.os_agnostic
@pytest.mark.parametrize(("import_line", "call"), EVADING_SPELLINGS)
def test_an_aliased_or_from_imported_spelling_is_refused_the_same_as_the_literal_one(
    import_line: str, call: str
) -> None:
    source = f"{import_line}\n\ndef program(co, args):\n    return {call}\n"

    with pytest.raises(NondeterministicCallError, match="line 4"):
        assert_deterministic(module_from(source))


@pytest.mark.os_agnostic
@pytest.mark.parametrize("module_name", ["time", "random", "uuid", "os", "secrets", "datetime"])
def test_a_star_import_from_a_forbidden_module_is_refused_outright(module_name: str) -> None:
    # A star import binds an unknown set of names, so the alias map has nothing to
    # resolve against - this must be refused on the import line itself, before any
    # call through one of those names is ever reached.
    source = f"from {module_name} import *\n\ndef program(co, args):\n    return None\n"

    with pytest.raises(NondeterministicCallError, match="line 1") as info:
        assert_deterministic(module_from(source))

    assert f"from {module_name} import *" in str(info.value)


@pytest.mark.os_agnostic
def test_a_call_on_a_call_is_skipped_rather_than_crashing_the_check() -> None:
    # foo() is not a name the alias map or the forbidden set can judge; assert_deterministic
    # must skip it, not raise TypeError/AttributeError walking the chain.
    module = module_from("def program(co, args):\n    def foo():\n        return None\n    return foo().now()\n")

    assert_deterministic(module)  # unresolvable call chain: skipped, not refused, not crashed


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
