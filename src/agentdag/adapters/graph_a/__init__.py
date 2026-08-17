"""Adapters implementing the graph A ports.

Contents:
    * :class:`~.git_cli.GitCli` - GitPort over the git CLI.
    * :class:`~.gate_make.MakeTestGate` - GatePort running the project's test gate.
    * :class:`~.store_fs.FsRunStore` - RunStore on the filesystem.
    * :class:`~.work_claude_sdk.ClaudeSdkWork` - WorkPort over the Claude Agent SDK.
    * :class:`~.approve_console.ConsoleApprove` - ApprovePort on the console.
"""

from __future__ import annotations

from .approve_console import ConsoleApprove
from .gate_make import MakeTestGate
from .git_cli import GitCli
from .store_fs import FsRunStore
from .work_claude_sdk import ClaudeSdkWork

__all__ = ["ClaudeSdkWork", "ConsoleApprove", "FsRunStore", "GitCli", "MakeTestGate"]
