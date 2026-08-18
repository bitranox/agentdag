"""RED/GREEN tests for the run directory's exclusive file lock (design 3.4)."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from agentdag.adapters.kernel.lock_file import FileRunLock, current_holder, holder_is_alive
from agentdag.domain.kernel_errors import LockHeld
from agentdag.domain.models import LockHolder

if TYPE_CHECKING:
    from pathlib import Path

BOOT_ID_UNREADABLE = current_holder().boot_id == "-"
"""Whether this host records a real boot id at all; ``"-"`` means it could not be read
(off Linux, or with no ``/proc``), and the boot comparison is then inert BY DESIGN."""


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


@pytest.mark.os_agnostic
def test_release_of_a_broken_stale_lock_does_not_touch_the_new_holder(tmp_path: Path) -> None:
    """acquire() breaking a stale lock hands back a token for the OLD holder.

    If a second process races in afterwards and takes the lock for itself, the
    first process's (now-stale) token must not be able to unlink that second
    process's lock - release() only unlinks a lock file that still names its
    own token's holder.
    """
    me = current_holder()
    dead = LockHolder(host=me.host, boot_id=me.boot_id, pid=2**22 - 1, pid_start_time="1")
    (tmp_path / "lock").write_text(dead.model_dump_json())

    stale_token = FileRunLock().acquire(tmp_path, me)  # breaks the dead holder's lock, returns token T1

    other = me.model_copy(update={"pid_start_time": me.pid_start_time + "-other"})
    (tmp_path / "lock").write_text(other.model_dump_json())  # a different process re-acquired it

    FileRunLock().release(stale_token)

    assert (tmp_path / "lock").exists()
    assert json.loads((tmp_path / "lock").read_text())["pid_start_time"] == other.pid_start_time


@pytest.mark.os_linux
@pytest.mark.skipif(BOOT_ID_UNREADABLE, reason="no /proc boot id to compare against")
def test_a_holder_from_a_previous_boot_is_dead_whatever_its_pid_says() -> None:
    # THIS process, so the pid exists and its start time matches - every liveness signal
    # except the boot id says alive. After a reboot that pid belongs to some unrelated
    # process, which is exactly the case the pid test cannot see through, so the lock would
    # be reported held forever by a coordinator that died with the previous boot.
    me = current_holder()
    assert holder_is_alive(me)

    from_last_boot = me.model_copy(update={"boot_id": "00000000-0000-0000-0000-000000000000"})

    assert not holder_is_alive(from_last_boot)
    # An unknown boot id on either side proves nothing, so it must NOT count as a difference.
    assert holder_is_alive(me.model_copy(update={"boot_id": "-"}))


@pytest.mark.os_linux
@pytest.mark.skipif(BOOT_ID_UNREADABLE, reason="no /proc boot id to compare against")
def test_a_lock_left_by_a_previous_boot_is_broken_rather_than_held(tmp_path: Path) -> None:
    me = current_holder()
    from_last_boot = me.model_copy(update={"boot_id": "00000000-0000-0000-0000-000000000000"})
    (tmp_path / "lock").write_text(from_last_boot.model_dump_json())

    FileRunLock().acquire(tmp_path, me)  # no LockHeld: the machine rebooted since that holder

    assert json.loads((tmp_path / "lock").read_text())["boot_id"] == me.boot_id
