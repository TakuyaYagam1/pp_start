from __future__ import annotations

import re
import subprocess
from pathlib import Path


from app.config import Settings
from app.core.models import ActionMode
from app.observability.logging import (
    close_logger_handlers,
    configure_logging,
    log_app_event,
)
from pydantic import SecretStr

TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")
OPENROUTER_KEY_RE = re.compile(r"\b" + "sk" + r"-or-v1-[A-Za-z0-9_-]{20,}\b")


def _repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        Path(line)
        for line in result.stdout.splitlines()
        if line and Path(line).is_file()
    ]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_env_files_are_ignored_except_example() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert re.search(r"(?m)^\.env$", gitignore)
    assert re.search(r"(?m)^\.env\.\*$", gitignore)
    assert re.search(r"(?m)^!\.env\.example$", gitignore)
    assert Path(".env") not in _repository_files()


def test_env_example_contains_only_placeholders() -> None:
    env_example = Path(".env.example")
    text = _read_text(env_example)

    assert "BOT_TOKEN=replace_me" in text
    assert "LLM_API_KEY=replace_me" in text
    assert TELEGRAM_BOT_TOKEN_RE.search(text) is None
    assert OPENROUTER_KEY_RE.search(text) is None
    assert "Bearer " not in text


def test_repository_files_do_not_contain_common_runtime_secrets() -> None:
    leaks: list[str] = []
    for path in _repository_files():
        text = _read_text(path)
        if TELEGRAM_BOT_TOKEN_RE.search(text) or OPENROUTER_KEY_RE.search(text):
            leaks.append(path.as_posix())

    assert leaks == []


def test_readme_warns_not_to_commit_secrets() -> None:
    readme = _read_text(Path("README.md")).casefold()

    assert ".env" in readme
    assert "не коммит" in readme
    assert "секрет" in readme


def test_structured_logs_escape_user_controlled_newlines(tmp_path: Path) -> None:
    log_file = tmp_path / "spam.log"
    settings = Settings(
        bot_token=SecretStr("123456:test-token"),
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=ActionMode.NOTIFY_ADMIN,
        admin_username="@admin",
        llm_api_key=SecretStr("llm-secret"),
        llm_base_url="https://llm.example/v1",
        llm_model="test-model",
        llm_timeout_seconds=5,
        log_level="INFO",
        log_file=str(log_file),
    )
    logger = configure_logging(settings)

    try:
        log_app_event(
            logger,
            event="spam_detected",
            chat_id=-100123,
            user_id=42,
            message_text="first line\nforged level=ERROR",
            action="notify_admin",
            details="reason=test\rnext",
        )
    finally:
        for handler in logger.handlers:
            handler.flush()
        close_logger_handlers(logger)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "first line\\nforged level=ERROR" in lines[0]
    assert "reason=test\\rnext" in lines[0]
