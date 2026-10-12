"""Digit-only .env values reach every text setting as the text the operator wrote.

lib_layered_config's dotenv and environment layers turn an unquoted value into an int or float
whenever that number reads back as the same text (``2770`` stays ``2770``). A pydantic ``str``
field is strict about type, so every model that validates a configuration section must turn such
a number back into its digits; otherwise an ordinary ``.env`` line such as ``LIB_LOG_RICH__SERVICE=42``
stops the program at startup.

These tests load a real ``.env`` through the real loader, so a change in the library's coercion
shows up here rather than on a host.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr, ValidationError

from agentdag.adapters.config.loader import get_config
from agentdag.adapters.email.config import load_email_config_from_dict
from agentdag.adapters.logging.setup import (
    _build_runtime_config,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from lib_layered_config import Config


def _load_dotenv(tmp_path: Path, line: str) -> Config:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{line}\n", encoding="utf-8")
    return get_config(dotenv_path=str(dotenv))


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("env_key", "attr"),
    [("LIB_LOG_RICH__SERVICE", "service"), ("LIB_LOG_RICH__ENVIRONMENT", "environment")],
)
def test_a_digit_only_logging_name_is_read_as_its_digits(
    user_layer_in_tmp_path: Path, clear_config_cache: None, env_key: str, attr: str
) -> None:
    config = _load_dotenv(user_layer_in_tmp_path, f"{env_key}=2026")
    # Premise: the library hands the value over as a number. Without it the test proves nothing.
    assert isinstance(config.get("lib_log_rich", {})[attr], int)

    runtime = _build_runtime_config(config)  # pyright: ignore[reportPrivateUsage]

    assert getattr(runtime, attr) == "2026"


@pytest.mark.os_agnostic
@pytest.mark.parametrize(
    ("env_key", "attr", "expected"),
    [
        ("EMAIL__SMTP_PASSWORD", "smtp_password", SecretStr("123456")),
        ("EMAIL__SMTP_USERNAME", "smtp_username", "4711"),
        ("EMAIL__LOCAL_HOSTNAME", "smtp_local_hostname", "10"),
    ],
)
def test_a_digit_only_email_setting_is_read_as_its_digits(
    user_layer_in_tmp_path: Path,
    clear_config_cache: None,
    env_key: str,
    attr: str,
    expected: object,
) -> None:
    config = _load_dotenv(
        user_layer_in_tmp_path,
        f"{env_key}={expected.get_secret_value() if isinstance(expected, SecretStr) else expected}",
    )
    section = config.get("email", {})
    key = env_key.split("__", 1)[1].lower()
    assert isinstance(section[key], int)

    loaded = load_email_config_from_dict(config.as_dict())

    assert getattr(loaded, attr) == expected


@pytest.mark.os_agnostic
def test_a_numeric_email_setting_still_reads_as_a_number(
    user_layer_in_tmp_path: Path, clear_config_cache: None
) -> None:
    config = _load_dotenv(user_layer_in_tmp_path, "EMAIL__TIMEOUT=45")

    assert load_email_config_from_dict(config.as_dict()).smtp_timeout == 45.0


@pytest.mark.os_agnostic
def test_an_invalid_email_setting_is_still_refused(user_layer_in_tmp_path: Path, clear_config_cache: None) -> None:
    config = _load_dotenv(user_layer_in_tmp_path, "EMAIL__FROM_ADDRESS=not-an-address")

    with pytest.raises(ValidationError):
        load_email_config_from_dict(config.as_dict())
