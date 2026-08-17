"""CLI command implementations.

Collects all subcommand functions and re-exports them for registration
with the root CLI group.

Contents:
    * Info commands from :mod:`.info`
    * Config commands from :mod:`.config`
    * Email commands from :mod:`.email` (subpackage)
    * Graph A commands from :mod:`.graph_a`
    * Logging commands from :mod:`.logging`
"""

from __future__ import annotations

from .config import cli_config, cli_config_deploy, cli_config_generate_examples
from .email import cli_send_email, cli_send_notification
from .graph_a import cli_graph_a
from .info import cli_fail, cli_hello, cli_info
from .logging import cli_logdemo

__all__ = [
    "cli_config",
    "cli_config_deploy",
    "cli_config_generate_examples",
    "cli_fail",
    "cli_graph_a",
    "cli_hello",
    "cli_info",
    "cli_logdemo",
    "cli_send_email",
    "cli_send_notification",
]
