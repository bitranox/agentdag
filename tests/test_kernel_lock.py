"""RED/GREEN tests for the run directory's exclusive file lock (design 3.4)."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder, holder_is_alive
from agentdag.domain.errors import LockHeld
from agentdag.domain.models import LockHolder

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.os_agnostic
def test_lock_is_exclusive_and_records_the_holder(tmp_path: Path) -> None:
    lock = FileRunLock()
    me = current_holder()

    token = lock.acquire(tmp_path, me)

    assert json.loads((tmp_path / "lock").read_text())["pid"] == os.getpid()
    with pytest.raises(LockHeld):
        lock.acquire(tmp_path, me)  # a second coordinator on the same run dir is refused

    lock.release(token)
    lock.acquire(tmp_path, me)  # after release the dir is free again


@pytest.mark.os_agnostic
def test_a_stale_lock_of_a_dead_holder_is_broken(tmp_path: Path) -> None:
    dead = LockHolder(host=current_holder().host, boot_id=current_holder().boot_id, pid=2**22 - 1, pid_start_time="1")
    (tmp_path / "lock").write_text(dead.model_dump_json())

    assert not holder_is_alive(dead)
    FileRunLock().acquire(tmp_path, current_holder())  # no LockHeld: the recorded process is proven gone


@pytest.mark.os_agnostic
def test_a_live_pid_with_a_different_start_time_is_not_the_holder() -> None:
    me = current_holder()
    assert holder_is_alive(me)
    # a reused pid is never the test on its own
    assert not holder_is_alive(me.model_copy(update={"pid_start_time": me.pid_start_time + "x"}))
